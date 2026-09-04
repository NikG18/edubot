"""Небольшая CLI для подготовки и smoke-теста агентских чеков.

Скрипт не читает systemd и не печатает секреты. DATABASE_URL и T-Bank credentials
должны быть уже переданы процессу окружением (для server smoke их можно безопасно
унаследовать из unit через shell-команду из FISCAL_AGENT.md).
"""

import argparse
import asyncio
import json

import database as db
from fiscal_agent import ensure_fiscal_schema, normalize_supplier_phone


async def _set_phone(tutor_id: int, phone: str):
    normalized = normalize_supplier_phone(phone)
    if not normalized:
        raise SystemExit("Некорректный телефон. Используйте, например, +79991234567")
    await db.init_db()
    await ensure_fiscal_schema()
    async with db._legacy.pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE tutors SET phone=$1 WHERE id=$2 RETURNING id,name,inn,phone",
            normalized, int(tutor_id),
        )
    if not row:
        raise SystemExit(f"Репетитор id={tutor_id} не найден")
    print(json.dumps({
        "id": int(row["id"]),
        "name": row["name"],
        "inn_present": bool(row["inn"]),
        "phone": row["phone"],
    }, ensure_ascii=False))
    await db.close_db()


async def _list_tutors():
    await db.init_db()
    await ensure_fiscal_schema()
    async with db._legacy.pool.acquire() as conn:
        rows = await conn.fetch("SELECT id,name,inn,phone FROM tutors ORDER BY id")
    result = [{
        "id": int(r["id"]),
        "name": r["name"],
        "inn_present": bool((r["inn"] or "").strip()),
        "phone_present": bool(normalize_supplier_phone(r["phone"])),
    } for r in rows]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    await db.close_db()


async def _close_booking(booking_id: int, force_test: bool):
    await db.init_db()
    from fiscal_receipts import send_booking_closing_receipt
    result = await send_booking_closing_receipt(int(booking_id), allow_noncompleted=force_test)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    await db.close_db()


def _preview(args):
    from payments import build_agent_receipt
    receipt = build_agent_receipt(
        amount_kop=args.amount,
        description=args.description,
        customer_email=args.email,
        tutor_name=args.tutor_name,
        inn=args.inn,
        supplier_phone=normalize_supplier_phone(args.phone),
        payment_method="full_payment" if args.closing else "full_prepayment",
        closing=args.closing,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Агентские фискальные smoke-инструменты")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-tutors", help="Показать наличие ИНН/телефона без вывода ИНН")

    p = sub.add_parser("set-phone", help="Сохранить телефон поставщика-репетитора")
    p.add_argument("tutor_id", type=int)
    p.add_argument("phone")

    p = sub.add_parser("close-booking", help="Отправить идемпотентный закрывающий чек")
    p.add_argument("booking_id", type=int)
    p.add_argument("--force-test", action="store_true", help="Только smoke: не требовать status=completed")

    p = sub.add_parser("preview", help="Локально показать JSON чека без запроса в банк")
    p.add_argument("--amount", type=int, required=True, help="Сумма в копейках")
    p.add_argument("--description", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--tutor-name", required=True)
    p.add_argument("--inn", required=True)
    p.add_argument("--phone", required=True)
    p.add_argument("--closing", action="store_true")

    args = parser.parse_args()
    if args.cmd == "list-tutors":
        asyncio.run(_list_tutors())
    elif args.cmd == "set-phone":
        asyncio.run(_set_phone(args.tutor_id, args.phone))
    elif args.cmd == "close-booking":
        asyncio.run(_close_booking(args.booking_id, args.force_test))
    elif args.cmd == "preview":
        _preview(args)


if __name__ == "__main__":
    main()
