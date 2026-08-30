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
    """Строит ФФД 1.2 чек для услуги самозанятого принципала через агента.

    Для агентского договора используется AgentSign=another: это не платежный агент,
    не комиссионер и не поверенный по отдельному договору поручения. Поставщиком
    услуги в SupplierInfo является непосредственный репетитор.
    """
    if amount_kop <= 0:
        raise ValueError("Сумма чека должна быть положительной")
    name, supplier_inn, phone = _validate_supplier(tutor_name, inn, supplier_phone)
    email = (customer_email or "").strip()
    if not email:
        raise ValueError("Для электронного чека нужен e-mail покупателя")

    item = {
        "Name": description[:128],
        "Price": int(amount_kop),
        "Quantity": 1,
        "Amount": int(amount_kop),
        "Tax": "none",
        "PaymentMethod": payment_method,
        "PaymentObject": "service",
        "AgentData": {"AgentSign": "another"},
        "SupplierInfo": {
            "Phones": [phone],
            "Name": name,
            "Inn": supplier_inn,
        },
    }
    receipt = {
        "Email": email,
        # T-Bank принимает usn_income и по ИНН автоматически определяет АУСН.
        "Taxation": "usn_income",
        "Items": [item],
    }
    if closing:
        # Деньги уже были получены как 100% предоплата. В закрывающем чеке новой
        # безналичной оплаты нет: расчет закрывается ранее внесенной предоплатой.
        receipt["Payments"] = {
            "Electronic": 0,
            "AdvancePayment": int(amount_kop),
        }
    return receipt


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
    """Создаёт одностадийный СБП-платёж с агентским чеком 100% предоплаты."""
    if amount_kop <= 0:
        logging.error("Некорректная сумма платежа: %s", amount_kop)
        return None, None

    try:
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
    """Отправляет чек полного расчета после фактического оказания занятия."""
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
