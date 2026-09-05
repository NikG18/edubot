"""Compatibility alias for archived database legacy implementation.

The implementation lives in archive/database_legacy.py. Remove this alias only
after database.py no longer depends on the legacy compatibility layer.

Old PostgreSQL installations may predate the UNIQUE(tutor_id, name) constraint on
``subjects``. ``CREATE TABLE IF NOT EXISTS`` does not retrofit that constraint, but
legacy ``add_subject`` uses ``ON CONFLICT(tutor_id, name)``. Ensure the required
unique index exists during normal database initialization before exposing the
archived implementation under its compatibility module name.
"""
import sys as _sys
from archive import database_legacy as _impl

_original_init_db = _impl.init_db
_subjects_schema_ready = False


async def _init_db_with_subject_schema():
    global _subjects_schema_ready
    await _original_init_db()
    if _subjects_schema_ready:
        return

    async with _impl.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('subjects-schema-v1', 0))"
            )
            # get_all_tutors() reads subjects ordered by id and the last duplicate
            # wins in its name->price mapping. Keep that same effective row by
            # preserving the greatest id for each tutor/name pair.
            await conn.execute(
                """
                DELETE FROM subjects older
                USING subjects newer
                WHERE older.tutor_id = newer.tutor_id
                  AND older.name = newer.name
                  AND older.id < newer.id
                """
            )
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_subject_tutor_name
                ON subjects(tutor_id, name)
                """
            )
    _subjects_schema_ready = True


_impl.init_db = _init_db_with_subject_schema
_sys.modules[__name__] = _impl
