import os
import json
import hashlib
import logging
import aiohttp
import ssl
import certifi
from typing import Optional, Tuple

TINKOFF_TERMINAL_KEY = os.environ.get("TINKOFF_TERMINAL_KEY")
TINKOFF_SECRET_KEY = os.environ.get("TINKOFF_SECRET_KEY")
TINKOFF_WEBHOOK_URL = os.environ.get("TINKOFF_WEBHOOK_URL", "")
API_BASE = "https://securepay.tinkoff.ru/v2/"

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


async def api_call(endpoint: str, params: dict) -> dict:
    if not TINKOFF_TERMINAL_KEY or not TINKOFF_SECRET_KEY:
        logging.error("T-Bank credentials are not configured")
        return {}

    payload = dict(params)
    payload["TerminalKey"] = TINKOFF_TERMINAL_KEY
    payload["Token"] = generate_token(payload)

    timeout = aiohttp.ClientTimeout(total=15, connect=5)
    connector = aiohttp.TCPConnector(ssl=_ssl_context())
    url = API_BASE + endpoint

    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logging.error("T-Bank %s HTTP %s: %s", endpoint, resp.status, text[:500])
                    return {}
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    logging.error("T-Bank %s returned non-JSON: %s", endpoint, text[:500])
                    return {}
    except (aiohttp.ClientError, TimeoutError) as exc:
        logging.error("T-Bank %s network error: %s", endpoint, exc)
        return {}


async def get_sbp_payment_link(payment_id: str) -> Optional[str]:
    """Возвращает функциональную ссылку СБП для уже существующего PaymentId.

    Новый Init не создаётся: ссылку можно повторно получить для того же платежа и
    снова показать пользователю, если прежнее сообщение в мессенджере потерялось.
    """
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
    order_id_prefix: str = "booking",
) -> Tuple[Optional[str], Optional[str]]:
    """Создаёт одностадийный платёж и возвращает ссылку СБП, а не карточную форму."""
    if amount_kop <= 0:
        logging.error("Некорректная сумма платежа: %s", amount_kop)
        return None, None

    order_id = f"{order_id_prefix}_{booking_id}_{int(__import__('time').time() * 1000)}"
    sbp_description = description[:140]
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
        logging.error("T-Bank Init failed: ErrorCode=%s Details=%s Message=%s",
                      resp.get("ErrorCode"), resp.get("Details"), resp.get("Message"))
        return None, None

    payment_id = str(resp["PaymentId"])
    sbp_link = await get_sbp_payment_link(payment_id)
    if not sbp_link:
        # PaymentId возвращаем вызывающему коду, чтобы платеж не потерялся и не
        # создавался повторный Init при временной ошибке GetQr.
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
