import logging
from tinkoff_payment import TinkoffPayment
import aiosqlite
import os

TINKOFF_TERMINAL_KEY = os.environ.get("TINKOFF_TERMINAL_KEY")
TINKOFF_SECRET_KEY = os.environ.get("TINKOFF_SECRET_KEY")

tinkoff = TinkoffPayment(
    terminal_key=TINKOFF_TERMINAL_KEY,
    password=TINKOFF_SECRET_KEY,
    API_BASE = "https://rest-api-test.tinkoff.ru/v2/"
)

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
        "Taxation": "usn_income",  # замените на вашу систему
        "Items": [{
            "Name": description[:64],
            "Price": amount_kop,
            "Quantity": 1,
            "Amount": amount_kop,
            "Tax": "none"
        }]
    }
    if inn:
        receipt["AgentSign"] = "agent"
        receipt["AgentData"] = {
            "AgentPhone": "+79331209603",
            "SupplierInfo": {
                "Name": tutor_name,
                "Inn": inn,
                "Phones": ["+79331209603"]
            }
        }

    payload = {
        "Amount": amount_kop,
        "OrderId": f"booking_{booking_id}",
        "Description": description,
        "Receipt": receipt
    }
    try:
        resp = tinkoff.init(payload)
        if resp.get("Success"):
            return resp["PaymentURL"], resp["PaymentId"]
        else:
            logging.error(f"Init failed: {resp.get('Details')}")
            return None, None
    except Exception as e:
        logging.error(f"Tinkoff init error: {e}")
        return None, None

async def check_payment(payment_id: str) -> dict:
    try:
        resp = tinkoff.get_state({"PaymentId": payment_id})
        return resp
    except Exception as e:
        logging.error(f"Check payment error: {e}")
        return {}
