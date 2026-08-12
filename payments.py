import os
import json
import hashlib
import logging
import aiohttp
import certifi
import ssl

TINKOFF_TERMINAL_KEY = os.environ.get("TINKOFF_TERMINAL_KEY")
TINKOFF_SECRET_KEY = os.environ.get("TINKOFF_SECRET_KEY")
TINKOFF_WEBHOOK_URL = os.environ.get("TINKOFF_WEBHOOK_URL")
# Для боевого API
API_BASE = "https://securepay.tinkoff.ru/v2/"

def generate_token(params: dict) -> str:
    params1 = dict(params)
    params1["TerminalKey"] = TINKOFF_TERMINAL_KEY
    params1["Password"] = TINKOFF_SECRET_KEY
    excluded_keys = {"Token", "DATA", "Shops", "Receipts", "PaymentMethods"} #"Receipt"
    data = {k: v for k, v in sorted(params1.items())
            if k not in excluded_keys and not k.startswith("DATA.")}
    values = []
    for v in data.values():
        if isinstance(v, dict):
            values.append(json.dumps(v, separators=(',', ':')))
        else:
            values.append(str(v))
    request_string = ''.join(values)
    token = hashlib.sha256((request_string).encode('utf-8')).hexdigest()
    return token


async def api_call(endpoint: str, params: dict) -> dict:
    url = API_BASE + endpoint
    params["TerminalKey"] = TINKOFF_TERMINAL_KEY
    params["Token"] = generate_token(params)
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False), timeout=timeout) as session:
        try:
            async with session.post(url, json=params) as resp:
                text = await resp.text()
                if resp.status == 200:
                    return json.loads(text)
                else:
                    logging.error(f"API error {endpoint}: {resp.status} {text[:200]}")
                    return {}
        except Exception as e:
            logging.error(f"Tinkoff API error ({endpoint}): {e}")
            return {}

async def create_payment(booking_id: int, amount_kop: int, description: str, tutor_id: int, tutor_name: str, customer_email: str, inn: str = None) -> tuple:
    receipt = {
        "Email": customer_email,
        "Taxation": "usn_income",
        "Items": [{
            "Name": description[:64],
            "Price": amount_kop,
            "Quantity": 1,
            "Amount": amount_kop,
            "Tax": "none"
        }]
    }
    if inn:
        receipt["Items"][0]["AgentSign"] = "agent"
        receipt["Items"][0]["AgentData"] = {
            "AgentPhone": "+79331209603",          # твой телефон как агента
            "SupplierInfo": {
                "Name": tutor_name,
                "Inn": inn,
                "Phones": []
            }
        }
    params = {
       # "TerminalKey": TINKOFF_TERMINAL_KEY,
        "Amount": amount_kop,
        "OrderId": f"booking_{booking_id}",
        "Description": description,
        "Receipt": receipt,
        "NotificationURL": os.environ.get("TINKOFF_WEBHOOK_URL", "")
    }

    resp = await api_call("Init", params)
    if resp.get("Success"):
        return resp["PaymentURL"], resp["PaymentId"]
    else:
        logging.error(f"Init failed: {resp.get('Details')}")
        return None, None


async def check_payment(payment_id: str) -> dict:
    params = {"PaymentId": payment_id}
    return await api_call("GetState", params)
