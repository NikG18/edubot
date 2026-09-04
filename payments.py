import os
import json
import hashlib
import logging
import aiohttp
import ssl
from typing import Optional, Tuple

TINKOFF_TERMINAL_KEY = os.environ.get("TINKOFF_TERMINAL_KEY")
TINKOFF_SECRET_KEY = os.environ.get("TINKOFF_SECRET_KEY")
TINKOFF_WEBHOOK_URL = os.environ.get("TINKOFF_WEBHOOK_URL", "")
API_BASE = "https://securepay.tinkoff.ru/v2/"
CASHBOX_BASE = "https://securepay.tinkoff.ru/cashbox/"
FULL_REFUND_STATUSES = frozenset({"REFUNDED", "REVERSED"})

# Если занятие оказывает сам владелец ККТ/ИП, это обычная реализация собственной
# услуги, а не агентская операция. Значения можно переопределить окружением, но
# production-default соответствует реквизитам ИП Ганжи Никиты Тимуровича.
OPERATOR_INN = os.environ.get("FISCAL_OPERATOR_INN", "390612116215").strip()
OPERATOR_NAME = os.environ.get("FISCAL_OPERATOR_NAME", "ИП Ганжа Никита Тимурович").strip()
OPERATOR_PHONE = os.environ.get("FISCAL_OPERATOR_PHONE", "+79331209603").strip()

if not TINKOFF_TERMINAL_KEY or not TINKOFF_SECRET_KEY:
    logging.warning("TINKOFF_TERMINAL_KEY/TINKOFF_SECRET_KEY не заданы")


def generate_token(params: dict) -> str:
    """Токен T-Bank: только значения полей верхнего уровня, без вложенных объектов."""
    flat = dict(params)
    flat["TerminalKey"] = TINKOFF_TERMINAL_KEY
    flat["Password"] = TINKOFF_SECRET_KEY

    excluded = {"Token", "DATA", "Receipt", "Receipts", "Shops", "PaymentMethods"}
    values = []
    for key in sorted(flat):
        if key in excluded or key.startswith("DATA."):
            continue
        value = flat[key]
        if isinstance(value, (dict, list)) or value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        values.append(str(value))
    return hashlib.sha256("".join(values).encode("utf-8")).hexdigest()


def _ssl_context():
    return ssl.create_default_context()


async def _post(url: str, payload: dict, operation: str) -> dict:
    timeout = aiohttp.ClientTimeout(total=15, connect=5)
    connector = aiohttp.TCPConnector(ssl=_ssl_context())
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logging.error("T-Bank %s HTTP %s: %s", operation, resp.status, text[:500])
                    return {}
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    logging.error("T-Bank %s returned non-JSON: %s", operation, text[:500])
                    return {}
    except (aiohttp.ClientError, TimeoutError) as exc:
        logging.error("T-Bank %s network error: %s", operation, exc)
        return {}


async def api_call(endpoint: str, params: dict) -> dict:
    if not TINKOFF_TERMINAL_KEY or not TINKOFF_SECRET_KEY:
        logging.error("T-Bank credentials are not configured")
        return {}

    payload = dict(params)
    payload["TerminalKey"] = TINKOFF_TERMINAL_KEY
    payload["Token"] = generate_token(payload)
    return await _post(API_BASE + endpoint, payload, endpoint)


async def cashbox_call(endpoint: str, params: dict) -> dict:
    """Вызов методов /cashbox/* с той же подписью верхнеуровневых параметров."""
    if not TINKOFF_TERMINAL_KEY or not TINKOFF_SECRET_KEY:
        logging.error("T-Bank credentials are not configured")
        return {}
    payload = dict(params)
    payload["TerminalKey"] = TINKOFF_TERMINAL_KEY
    payload["Token"] = generate_token(payload)
    return await _post(CASHBOX_BASE + endpoint, payload, f"cashbox/{endpoint}")


def is_operator_tutor(inn: Optional[str]) -> bool:
    """True, если непосредственный исполнитель — сам ИП-владелец кассы."""
    normalized = str(inn or "").strip()
    return bool(OPERATOR_INN and normalized == OPERATOR_INN)


def _validate_customer_email(customer_email: str) -> str:
    email = (customer_email or "").strip()
    if not email:
        raise ValueError("Для электронного чека нужен e-mail покупателя")
    return email


def _validate_receipt_amount(amount_kop: int, payment_method: str, closing: bool):
    if amount_kop <= 0:
        raise ValueError("Сумма чека должна быть положительной")
    if payment_method not in {"full_prepayment", "full_payment"}:
        raise ValueError("Неподдерживаемый способ расчета для занятия")
    if closing and payment_method != "full_payment":
        raise ValueError("Закрывающий чек должен иметь способ расчета full_payment")


def _base_receipt_item(amount_kop: int, description: str, payment_method: str) -> dict:
    return {
        "Name": description[:128],
        "Price": int(amount_kop),
        "Quantity": 1,
        "Amount": int(amount_kop),
        "Tax": "none",
        "PaymentMethod": payment_method,
        "PaymentObject": "service",
        "MeasurementUnit": "шт",
    }


def _base_receipt(customer_email: str, item: dict, *, amount_kop: int, closing: bool) -> dict:
    receipt = {
        "FfdVersion": "1.2",
        "Email": _validate_customer_email(customer_email),
        # T-Bank принимает протокольное значение usn_income для данного магазина.
        "Taxation": "usn_income",
        "Items": [item],
    }
    if closing:
        receipt["Payments"] = {
            "Cash": 0,
            "Electronic": 0,
            "AdvancePayment": int(amount_kop),
            "Credit": 0,
            "Provision": 0,
        }
    return receipt


def _validate_supplier(tutor_name: str, inn: Optional[str], supplier_phone: Optional[str]):
    name = (tutor_name or "").strip()
    supplier_inn = (inn or "").strip()
    phone = (supplier_phone or "").strip()
    if not name:
        raise ValueError("Не указано имя/наименование поставщика услуги")
    if not (supplier_inn.isdigit() and len(supplier_inn) in {10, 12}):
        raise ValueError("ИНН поставщика должен содержать 10 или 12 цифр")
    if not (phone.startswith("+") and phone[1:].isdigit() and len(phone) <= 19):
        raise ValueError("Телефон поставщика должен быть в формате +<цифры>")
    return name[:200], supplier_inn, phone


def build_direct_receipt(
    *,
    amount_kop: int,
    description: str,
    customer_email: str,
    payment_method: str,
    closing: bool = False,
) -> dict:
    """Чек собственной услуги ИП без агентских реквизитов позиции."""
    _validate_receipt_amount(amount_kop, payment_method, closing)
    item = _base_receipt_item(amount_kop, description, payment_method)
    # Намеренно отсутствуют AgentData и SupplierInfo: исполнитель совпадает с
    # пользователем ККТ, поэтому это не агентская реализация.
    return _base_receipt(customer_email, item, amount_kop=amount_kop, closing=closing)


def build_agent_receipt(
    *,
    amount_kop: int,
    description: str,
    customer_email: str,
    tutor_name: str,
    inn: str,
    supplier_phone: str,
    payment_method: str,
    closing: bool = False,
) -> dict:
    """Строит чек ФФД 1.2 для услуги самозанятого принципала через агента."""
    _validate_receipt_amount(amount_kop, payment_method, closing)
    name, supplier_inn, phone = _validate_supplier(tutor_name, inn, supplier_phone)
    item = _base_receipt_item(amount_kop, description, payment_method)
    item.update({
        "AgentData": {"AgentSign": "another"},
        "SupplierInfo": {
            "Phones": [phone],
            "Name": name,
            "Inn": supplier_inn,
        },
    })
    return _base_receipt(customer_email, item, amount_kop=amount_kop, closing=closing)


async def get_sbp_payment_link(payment_id: str) -> Optional[str]:
    if not payment_id:
        return None
    resp = await api_call("GetQr", {
        "PaymentId": str(payment_id),
        "DataType": "PAYLOAD",
        "PaymentMethod": "SBP",
    })
    data = resp.get("Data")
    if data:
        return str(data)
    logging.error(
        "T-Bank GetQr(SBP) failed for payment_id=%s: ErrorCode=%s Details=%s Message=%s",
        payment_id,
        resp.get("ErrorCode"),
        resp.get("Details"),
        resp.get("Message"),
    )
    return None


async def create_payment(
    booking_id: int,
    amount_kop: int,
    description: str,
    tutor_id: int,
    tutor_name: str,
    customer_email: str,
    inn: Optional[str] = None,
    supplier_phone: Optional[str] = None,
    order_id_prefix: str = "booking",
) -> Tuple[Optional[str], Optional[str]]:
    """Создаёт СБП-платёж: прямой для услуг владельца ИП, агентский для остальных."""
    if amount_kop <= 0:
        logging.error("Некорректная сумма платежа: %s", amount_kop)
        return None, None

    direct_service = is_operator_tutor(inn)
    try:
        if direct_service:
            receipt = build_direct_receipt(
                amount_kop=amount_kop,
                description=description,
                customer_email=customer_email,
                payment_method="full_prepayment",
            )
        else:
            if not supplier_phone:
                try:
                    from fiscal_agent import get_tutor_phone
                    supplier_phone = await get_tutor_phone(int(tutor_id))
                except Exception:
                    logging.exception("Не удалось получить телефон поставщика tutor_id=%s", tutor_id)
                    return None, None
            receipt = build_agent_receipt(
                amount_kop=amount_kop,
                description=description,
                customer_email=customer_email,
                tutor_name=tutor_name,
                inn=inn or "",
                supplier_phone=supplier_phone or "",
                payment_method="full_prepayment",
            )
    except ValueError as exc:
        logging.error("Фискальные реквизиты платежа некорректны: %s", exc)
        return None, None

    order_id = f"{order_id_prefix}_{booking_id}_{int(__import__('time').time() * 1000)}"
    params = {
        "Amount": int(amount_kop),
        "OrderId": order_id,
        "Description": description[:140],
        "PayType": "O",
        "Receipt": receipt,
    }
    if TINKOFF_WEBHOOK_URL:
        params["NotificationURL"] = TINKOFF_WEBHOOK_URL

    resp = await api_call("Init", params)
    if not (resp.get("Success") and resp.get("PaymentId")):
        logging.error("T-Bank Init failed: ErrorCode=%s Details=%s Message=%s",
                      resp.get("ErrorCode"), resp.get("Details"), resp.get("Message"))
        return None, None

    payment_id = str(resp["PaymentId"])
    sbp_link = await get_sbp_payment_link(payment_id)
    if not sbp_link:
        return None, payment_id
    return sbp_link, payment_id


async def send_closing_receipt(
    *,
    payment_id: str,
    amount_kop: int,
    description: str,
    customer_email: str,
    tutor_name: str,
    inn: str,
    supplier_phone: str,
) -> dict:
    """Отправляет закрывающий чек с тем же прямым/агентским режимом, что и предоплата."""
    if is_operator_tutor(inn):
        receipt = build_direct_receipt(
            amount_kop=amount_kop,
            description=description,
            customer_email=customer_email,
            payment_method="full_payment",
            closing=True,
        )
    else:
        receipt = build_agent_receipt(
            amount_kop=amount_kop,
            description=description,
            customer_email=customer_email,
            tutor_name=tutor_name,
            inn=inn,
            supplier_phone=supplier_phone,
            payment_method="full_payment",
            closing=True,
        )
    return await cashbox_call("SendClosingReceipt", {
        "PaymentId": str(payment_id),
        "Receipt": receipt,
    })


async def cancel_full_payment(payment_id: str, *, external_request_id: str = None) -> dict:
    """Отправляет в T-Bank запрос на полный возврат исходного платежа.

    Amount и Receipt намеренно не передаются: это полный возврат. ExternalRequestId
    используется как устойчивый идентификатор повторной операции на стороне магазина.
    """
    payment_id = str(payment_id or "").strip()
    if not payment_id:
        return {}
    params = {"PaymentId": payment_id}
    if external_request_id:
        params["ExternalRequestId"] = str(external_request_id)[:255]
    return await api_call("Cancel", params)


async def check_payment(payment_id: str) -> dict:
    if not payment_id:
        return {}
    result = await api_call("GetState", {"PaymentId": str(payment_id)})
    if not result:
        logging.warning("GetState: пустой ответ для payment_id=%s", payment_id)
        return {}
    if not result.get("Success", False):
        logging.warning("GetState error for %s: %s", payment_id, result.get("Details"))
    return result


def _payment_status(response: dict) -> str:
    return str((response or {}).get("Status") or "").upper()


def _bank_error(response: dict) -> dict:
    response = response or {}
    return {
        "error_code": response.get("ErrorCode"),
        "message": response.get("Message"),
        "details": response.get("Details"),
    }


async def refund_full_payment(payment_id: str, *, external_request_id: str = None) -> dict:
    """Безопасный полный возврат с проверкой состояния до и после Cancel.

    Возвращает success=True только когда T-Bank уже показывает конечный статус
    REFUNDED/REVERSED. Если банк принял запрос, но ещё обрабатывает его, возвращает
    pending=True — окончание подтвердят webhook или резервный GetState.
    """
    payment_id = str(payment_id or "").strip()
    if not payment_id:
        return {"success": False, "stage": "validate", "error": "missing_payment_id"}

    state_before = await check_payment(payment_id)
    status_before = _payment_status(state_before)
    if state_before.get("Success") and status_before in FULL_REFUND_STATUSES:
        return {
            "success": True,
            "status": status_before,
            "already_refunded": True,
            "state_before": state_before,
        }
    if not state_before or not state_before.get("Success"):
        return {
            "success": False,
            "stage": "get_state_before",
            "status": status_before,
            **_bank_error(state_before),
        }
    if status_before == "REFUNDING":
        return {
            "success": False,
            "pending": True,
            "stage": "await_refund",
            "status": status_before,
            "state_before": state_before,
        }
    if status_before not in {"CONFIRMED", "AUTHORIZED"}:
        return {
            "success": False,
            "stage": "precondition",
            "status": status_before,
            "error": "payment_not_confirmed",
        }

    cancel_response = await cancel_full_payment(
        payment_id,
        external_request_id=external_request_id,
    )
    cancel_status = _payment_status(cancel_response)
    if cancel_response.get("Success") and cancel_status in FULL_REFUND_STATUSES:
        return {
            "success": True,
            "status": cancel_status,
            "already_refunded": False,
            "cancel_response": cancel_response,
        }

    state_after = await check_payment(payment_id)
    status_after = _payment_status(state_after)
    if state_after.get("Success") and status_after in FULL_REFUND_STATUSES:
        return {
            "success": True,
            "status": status_after,
            "already_refunded": False,
            "cancel_response": cancel_response,
            "state_after": state_after,
        }

    if cancel_response.get("Success") or (
        state_after.get("Success")
        and status_after in {"CONFIRMED", "AUTHORIZED", "REFUNDING"}
    ):
        return {
            "success": False,
            "pending": True,
            "stage": "await_refund",
            "status": status_after or cancel_status or status_before,
            "cancel_response": cancel_response,
            "state_after": state_after,
        }

    error_source = cancel_response or state_after
    return {
        "success": False,
        "stage": "cancel",
        "status": status_after or cancel_status or status_before,
        "cancel_response": cancel_response,
        "state_after": state_after,
        **_bank_error(error_source),
    }


async def refund_booking_payment(booking_id: int, admin_id: int) -> dict:
    """Запрашивает полный возврат и синхронизирует его подтверждённый статус с БД."""
    from database import (
        add_booking_event,
        confirm_booking_refunded,
        get_booking,
        mark_booking_refund_pending,
    )

    booking = await get_booking(int(booking_id))
    if not booking:
        return {"success": False, "error": "booking_not_found"}
    refund_status = booking.get("refund_status") or "none"
    if refund_status == "refunded":
        return {"success": True, "already_refunded": True, "status": "REFUNDED"}
    if refund_status not in {"required", "pending"}:
        return {"success": False, "error": "refund_not_required"}

    payment_id = str(booking.get("tinkoff_payment_id") or "").strip()
    if not payment_id:
        logging.error("Невозможно вернуть booking=%s: отсутствует tinkoff_payment_id", booking_id)
        return {"success": False, "error": "missing_payment_id"}

    result = await refund_full_payment(
        payment_id,
        external_request_id=f"booking-refund-{int(booking_id)}",
    )
    if not result.get("success"):
        if result.get("pending"):
            changed, current = await mark_booking_refund_pending(int(booking_id), admin_id)
            if current and current.get("refund_status") == "refunded":
                return {**result, "success": True, "pending": False, "already_refunded": True}
            return {**result, "pending": True, "database_changed": changed}
        logging.error(
            "T-Bank refund failed booking=%s payment_id=%s stage=%s status=%s code=%s details=%s",
            booking_id,
            payment_id,
            result.get("stage"),
            result.get("status"),
            result.get("error_code"),
            result.get("details") or result.get("message") or result.get("error"),
        )
        try:
            await add_booking_event(
                int(booking_id),
                "refund_failed",
                old_status=booking.get("status"),
                new_status=booking.get("status"),
                actor_type="admin",
                actor_id=admin_id,
                details={
                    "payment_id": payment_id,
                    "bank_status": result.get("status"),
                    "error_code": result.get("error_code"),
                    "details": result.get("details") or result.get("message") or result.get("error"),
                },
            )
        except Exception:
            logging.exception("Не удалось записать refund_failed для booking=%s", booking_id)
        return result

    changed, current = await confirm_booking_refunded(
        int(booking_id),
        actor_type="admin",
        actor_id=admin_id,
    )
    if changed or (current and current.get("refund_status") == "refunded"):
        return {**result, "success": True, "database_changed": changed}
    return {**result, "success": False, "error": "database_state_not_eligible"}
