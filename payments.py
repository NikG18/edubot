import os
import json
import hashlib
import logging
import aiosqlite
import aiohttp

TINKOFF_TERMINAL_KEY = os.environ.get("TINKOFF_TERMINAL_KEY")
TINKOFF_SECRET_KEY = os.environ.get("TINKOFF_SECRET_KEY")

# Для боевого API
API_BASE = "https://securepay.tinkoff.ru/v2/"


def generate_token(params: dict) -> str:
    excluded_keys = {"Token", "Receipt", "DATA", "Shops", "Receipts", "PaymentMethods"}
    terminal_key= TINKOFF_TERMINAL_KEY
    password= TINKOFF_SECRET_KEY
    params = params+terminal_key+password
    data = {k: v for k, v in sorted(params.items())
            if k not in excluded_keys and not k.startswith("DATA.")}
    values = []
    for v in data.values():
        if isinstance(v, dict):
            values.append(json.dumps(v, separators=(',', ':')))
        else:
            values.append(str(v))
    request_string = ''.join(values)
    token = hashlib.sha256((request_string).encode()).hexdigest()
    return token


async def api_call(endpoint: str, params: dict) -> dict:
    url = API_BASE + endpoint
    logging.info(f"Используемый TerminalKey: {TINKOFF_TERMINAL_KEY}")
    logging.info(
        f"Используемый SecretKey (первые/последние 4 символа): {TINKOFF_SECRET_KEY[:4]}...{TINKOFF_SECRET_KEY[-4:]}")
    params["TerminalKey"] = TINKOFF_TERMINAL_KEY
    params["Token"] = generate_token(params)

    logging.info(f"Request to {url}")
    logging.info(f"Params: {json.dumps(params, indent=2)}")
    logging.info(f"Generated Token: {params['Token']}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=params, ssl=False) as resp:
                text = await resp.text()
                logging.info(f"Response status: {resp.status}")
                logging.info(f"Response body: {text[:500]}")
                if resp.status == 200:
                    return json.loads(text)
                else:
                    logging.error(f"API error {endpoint}: {resp.status} {text[:200]}")
                    return {}
        except Exception as e:
            logging.error(f"Tinkoff API error ({endpoint}): {e}")
            return {}


async def get_tutor_inn(tutor_id: int) -> str:
    async with aiosqlite.connect("bot.db") as db:
        cursor = await db.execute("SELECT inn FROM tutors WHERE id=?", (tutor_id,))
        row = await cursor.fetchone()
        return row[0] if row else ""


async def create_payment(booking_id: int, amount_kop: int, description: str,
                         tutor_id: int, tutor_name: str, customer_email: str) -> tuple:
    inn = await get_tutor_inn(tutor_id)
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
    #    if inn:
    #       receipt["AgentSign"] = "agent"
    #      receipt["AgentData"] = {
    #         "AgentPhone": "+70000000000",
    #        "SupplierInfo": {
    #           "Name": tutor_name,
    #          "Inn": inn,
    #         "Phones": ["+70000000001"]
    #    }
    # }

    params = {
       # "TerminalKey": TINKOFF_TERMINAL_KEY,
        "Amount": amount_kop,
        "OrderId": f"booking_{booking_id}",
        "Description": description
        #  "Receipt": receipt,
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
