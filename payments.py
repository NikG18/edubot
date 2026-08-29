import os
import json
import hashlib
import logging
import aiohttp
import ssl
from typing import Optional, Tuple

import fiscalization as _fiscal

TINKOFF_TERMINAL_KEY = os.environ.get("TINKOFF_TERMINAL_KEY")
TINKOFF_SECRET_KEY = os.environ.get("TINKOFF_SECRET_KEY")
TINKOFF_WEBHOOK_URL = os.environ.get("TINKOFF_WEBHOOK_URL", "")
API_BASE = "https://securepay.tinkoff.ru/v2/"
CASHBOX_BASE = "https://securepay.tinkoff.ru/cashbox/"

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


async def _post_tbank(url: str, params: dict, *, mark_transport_errors: bool = False) -> dict:
    if not TINKOFF_TERMINAL_KEY or not TINKOFF_SECRET_KEY:
        logging.error("T-Bank credentials are not configured")
        return {"_transport_error": True, "Message": "credentials not configured"} if mark_transport_errors else {}

    payload = dict(params)
    payload["TerminalKey"] = TINKOFF_TERMINAL_KEY
    payload["Token"] = generate_token(payload)

    timeout = aiohttp.ClientTimeout(total=20, connect=5)
    connector = aiohttp.TCPConnector(ssl=_ssl_context())
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    logging.error("T-Bank returned non-JSON HTTP %s: %s", resp.status, text[:500])
                    if mark_transport_errors:
                        return {"_transport_error": True, "Message": f"non-JSON HTTP {resp.status}"}
                    return {}

                if resp.status >= 500:
                    logging.error("T-Bank HTTP %s: %s", resp.status, text[:500])
                    if mark_transport_errors:
                        data = dict(data or {})
                        data["_transport_error"] = True
                        return data
                    return {}
                if resp.status >= 400:
                    logging.error("T-Bank HTTP %s: %s", resp.status, text[:500])
                    return data if mark_transport_errors else {}
                return data
    except (aiohttp.ClientError, TimeoutError) as exc:
        logging.error("T-Bank network error: %s", exc)
        if mark_transport_errors:
            return {"_transport_error": True, "Message": f"network error: {type(exc).__name__}"}
        return {}


async def api_call(endpoint: str, params: dict) -> dict:
    return await _post_tbank(API_BASE + endpoint, params)


async def cashbox_api_call(endpoint: str, params: dict) -> dict:
    """Вызов cashbox API с различением отказа банка и неизвестного transport result."""
    return await _post_tbank(CASHBOX_BASE + endpoint, params, mark_transport_errors=True)


async def get_sbp_payment_link(payment_id: str) -> Optional[str]:
    """Возвращает функциональную ссылку СБП для уже существующего PaymentId."""
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


async def send_closing_receipt(payment_id: str, receipt: dict) -> dict:
    """Отправляет закрывающий чек через официальный cashbox/SendClosingReceipt."""
    if not payment_id:
        return {"Success": False, "ErrorCode": "LOCAL", "Message": "PaymentId не задан"}
    return await cashbox_api_call("SendClosingReceipt", {
        "PaymentId": str(payment_id),
        "Receipt": receipt,
    })


async def create_payment(
    booking_id: int,
    amount_kop: int,
    description: str,
    tutor_id: int,
    tutor_name: str,
    customer_email: str,
    inn: Optional[str] = None,
    order_id_prefix: str = "booking",
) -> Tuple[Optional[str], Optional[str]]:
    """Создаёт одностадийный платёж и возвращает СБП-ссылку и PaymentId.

    При AGENT_FISCALIZATION_ENABLED=1 обычное занятие фискализируется как
    100% предоплата услуги принципала с AgentData/SupplierInfo. Абонементы в этом
    режиме намеренно блокируются до отдельной реализации поурочного зачёта аванса.
    """
    if amount_kop <= 0:
        logging.error("Некорректная сумма платежа: %s", amount_kop)
        return None, None

    if _fiscal.AGENT_FISCALIZATION_ENABLED and (order_id_prefix != "booking" or int(booking_id or 0) <= 0):
        logging.error(
            "Агентская фискализация абонементов пока заблокирована: нужен поурочный зачет аванса"
        )
        return None, None

    order_id = f"{order_id_prefix}_{booking_id}_{int(__import__('time').time() * 1000)}"
    sbp_description = description[:140]

    if _fiscal.AGENT_FISCALIZATION_ENABLED:
        try:
            profile = await _fiscal.require_tutor_fiscal_profile(tutor_id, expected_inn=inn)
            receipt = _fiscal.build_agent_prepayment_receipt(
                amount_kop=int(amount_kop),
                description=description,
                customer_email=customer_email,
                profile=profile,
            )
        except _fiscal.FiscalizationError as exc:
            logging.error("Фискальный preflight не пройден tutor_id=%s: %s", tutor_id, exc)
            return None, None
    else:
        # Старый безопасный режим оставлен до явного включения новой кассовой схемы.
        receipt = {
            "Email": customer_email,
            "Taxation": "usn_income",
            "Items": [{
                "Name": description[:64],
                "Price": int(amount_kop),
                "Quantity": 1,
                "Amount": int(amount_kop),
                "Tax": "none",
            }],
        }

    params = {
        "Amount": int(amount_kop),
        "OrderId": order_id,
        "Description": sbp_description,
        "PayType": "O",
        "Receipt": receipt,
    }
    if TINKOFF_WEBHOOK_URL:
        params["NotificationURL"] = TINKOFF_WEBHOOK_URL

    resp = await api_call("Init", params)
    if not (resp.get("Success") and resp.get("PaymentId")):
        logging.error(
            "T-Bank Init failed: ErrorCode=%s Details=%s Message=%s",
            resp.get("ErrorCode"), resp.get("Details"), resp.get("Message")
        )
        return None, None

    payment_id = str(resp["PaymentId"])

    if _fiscal.AGENT_FISCALIZATION_ENABLED:
        try:
            await _fiscal.record_booking_prepayment_snapshot(
                booking_id=int(booking_id),
                payment_id=payment_id,
                tutor_id=int(tutor_id),
                amount_kop=int(amount_kop),
                customer_email=customer_email,
                description=description,
                receipt=receipt,
            )
        except Exception:
            # PaymentId уже существует. Ссылку не выдаем: иначе можно получить оплату,
            # для которой мы потеряли связку с будущим закрывающим чеком.
            logging.exception(
                "Не удалось сохранить fiscal snapshot booking=%s payment=%s",
                booking_id, payment_id,
            )
            return None, payment_id

    sbp_link = await get_sbp_payment_link(payment_id)
    if not sbp_link:
        return None, payment_id
    return sbp_link, payment_id


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
