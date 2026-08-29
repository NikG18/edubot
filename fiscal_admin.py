#!/usr/bin/env python3
"""Административная утилита для подготовки агентской фискализации.

Не выводит DATABASE_URL, T-Bank secret key и другие секреты. Если переменные среды
не заданы в shell, может безопасно унаследовать Environment из systemd-сервиса.
"""

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
from datetime import date


def _load_systemd_environment(service: str) -> None:
    """Подтягивает Environment сервиса, не печатая его значения."""
    try:
        raw = subprocess.check_output(
            ["systemctl", "show", service, "-p", "Environment", "--value"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return
    if not raw:
        return
    for token in shlex.split(raw):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value


def _bootstrap_environment(service: str) -> None:
    if not os.environ.get("DATABASE_URL"):
        _load_systemd_environment(service)
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError(
            f"DATABASE_URL не найден ни в shell, ни в Environment сервиса {service}."
        )


async def _init_db():
    import database as db

    await db.init_db()
    return db


async def _close_db(db) -> None:
    await db.close_db()


async def _cmd_preflight(args) -> int:
    import fiscalization as fiscal

    db = await _init_db()
    try:
        rows = await fiscal.get_preflight_rows()
        print("Агентская фискализация:", "ON" if fiscal.AGENT_FISCALIZATION_ENABLED else "OFF")
        print("Taxation:", fiscal.FISCAL_TAXATION)
        print("AgentSign:", fiscal.FISCAL_AGENT_SIGN or "НЕ ЗАДАН")
        print("Профили репетиторов:")
        if not rows:
            print("  (репетиторов нет)")
            return 1
        ok = True
        for row in rows:
            flags = [
                "profile=OK" if row["profile"] else "profile=MISSING",
                "inn=OK" if row["inn_matches"] else "inn=MISMATCH",
                "npd=OK" if row["npd_verified"] else "npd=STALE/MISSING",
            ]
            print(f"  tutor_id={row['tutor_id']} {row['name']}: " + ", ".join(flags))
            ok = ok and row["profile"] and row["inn_matches"] and row["npd_verified"]
        if not fiscal.FISCAL_AGENT_SIGN:
            ok = False
        print("Итог:", "READY" if ok else "NOT READY")
        return 0 if ok else 2
    finally:
        await _close_db(db)


async def _cmd_set_profile(args) -> int:
    import fiscalization as fiscal

    db = await _init_db()
    try:
        tutors = await db.get_all_tutors()
        tutor = tutors.get(args.tutor_id)
        if not tutor:
            print(f"Репетитор tutor_id={args.tutor_id} не найден", file=sys.stderr)
            return 2
        existing_inn = (tutor.get("inn") or "").strip()
        if existing_inn and fiscal.validate_supplier_inn(existing_inn) != fiscal.validate_supplier_inn(args.inn):
            print("ИНН не совпадает с ИНН в карточке репетитора", file=sys.stderr)
            return 2
        await fiscal.set_tutor_fiscal_profile(
            args.tutor_id,
            supplier_name=args.name,
            supplier_inn=args.inn,
            supplier_phone=args.phone,
            npd_verified=False,
        )
        print(f"Fiscal profile tutor_id={args.tutor_id} сохранён. Статус НПД нужно проверить отдельно.")
        return 0
    finally:
        await _close_db(db)


async def _check_npd(inn: str, request_date: str) -> dict:
    import aiohttp

    url = "https://statusnpd.nalog.ru/api/v1/tracker/taxpayer_status"
    timeout = aiohttp.ClientTimeout(total=70, connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json={"inn": inn, "requestDate": request_date}) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"ФНС вернула HTTP {resp.status}")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError("ФНС вернула некорректный JSON") from exc
    if not isinstance(data.get("status"), bool):
        raise RuntimeError("В ответе ФНС нет boolean-поля status")
    return data


async def _cmd_verify_npd(args) -> int:
    import fiscalization as fiscal

    db = await _init_db()
    try:
        profile = await fiscal.get_tutor_fiscal_profile(args.tutor_id)
        if not profile:
            print("Сначала заполните fiscal profile репетитора", file=sys.stderr)
            return 2
        inn = fiscal.validate_supplier_inn(profile["supplier_inn"])
        request_date = args.date or date.today().isoformat()
        result = await _check_npd(inn, request_date)
        active = bool(result["status"])
        await fiscal.mark_tutor_npd_verified(args.tutor_id, active)
        # Ответ ФНС не содержит секретов; ИНН дополнительно не печатаем.
        print(f"tutor_id={args.tutor_id}: NPD={'ACTIVE' if active else 'NOT ACTIVE'} на {request_date}")
        print(result.get("message") or "")
        return 0 if active else 3
    finally:
        await _close_db(db)


async def _cmd_preview(args) -> int:
    import fiscalization as fiscal

    db = await _init_db()
    try:
        profile = await fiscal.require_tutor_fiscal_profile(args.tutor_id, expected_inn=args.inn)
        receipt = fiscal.build_agent_prepayment_receipt(
            amount_kop=args.amount_kop,
            description=args.description,
            customer_email=args.email,
            profile=profile,
        )
        if args.closing:
            receipt = fiscal.build_agent_closing_receipt(receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    finally:
        await _close_db(db)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Проверка и подготовка агентской фискализации")
    parser.add_argument(
        "--service",
        default="telegram-bot.service",
        help="systemd-сервис, из которого можно унаследовать Environment (по умолчанию telegram-bot.service)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("preflight", help="Проверить готовность фискальных профилей")
    p.set_defaults(func=_cmd_preflight)

    p = sub.add_parser("set-profile", help="Сохранить ФИО/ИНН/телефон поставщика")
    p.add_argument("tutor_id", type=int)
    p.add_argument("--name", required=True, help="Полное ФИО самозанятого")
    p.add_argument("--inn", required=True, help="ИНН самозанятого")
    p.add_argument("--phone", required=True, help="Телефон самозанятого")
    p.set_defaults(func=_cmd_set_profile)

    p = sub.add_parser("verify-npd", help="Проверить статус НПД через официальный сервис ФНС")
    p.add_argument("tutor_id", type=int)
    p.add_argument("--date", help="YYYY-MM-DD; по умолчанию сегодня")
    p.set_defaults(func=_cmd_verify_npd)

    p = sub.add_parser("preview", help="Показать JSON будущего чека без отправки в Т-Банк")
    p.add_argument("tutor_id", type=int)
    p.add_argument("--inn", required=True)
    p.add_argument("--amount-kop", required=True, type=int)
    p.add_argument("--email", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--closing", action="store_true", help="Показать закрывающий чек вместо предоплаты")
    p.set_defaults(func=_cmd_preview)
    return parser


async def _main_async(args) -> int:
    return await args.func(args)


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        _bootstrap_environment(args.service)
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Ошибка: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
