# vk_bot.py
import logging
import asyncio
from database import get_all_bookings
from datetime import datetime, timedelta
from vkbottle import Bot, Keyboard, KeyboardButtonColor, Text, Callback
from vkbottle.bot import Message     
from vkbottle.fsm.state import StatesGroup, State
from vkbottle.fsm import CtxStorage
from vkbottle.dispatch.rules.base import StateRule
import os
# Наш общий модуль
import core

# Токен сообщества ВК
BOT_TOKENVK = os.environ.get("BOT_TOKENVK")  # замените на реальный

bot = Bot(token=BOT_TOKENVK)
storage = CtxStorage()

# Состояния (будем хранить в storage вручную, не через dispenser)
class BookingStates:
    CHOOSING_TUTOR = "choosing_tutor"
    CHOOSING_SUBJECT = "choosing_subject"
    CHOOSING_DATE = "choosing_date"
    CHOOSING_TIME = "choosing_time"
    CONFIRMATION = "confirmation"

# ------------------------------------------------
# Главное меню
# ------------------------------------------------
MAIN_KEYBOARD = (
    Keyboard(one_time=False, inline=False)
    .add(Text("📝 Записаться"), color=KeyboardButtonColor.POSITIVE)
    .add(Text("📋 Мои записи"), color=KeyboardButtonColor.PRIMARY)
    .row()
    .add(Text("❓ Помощь"), color=KeyboardButtonColor.SECONDARY)
)

# ------------------------------------------------
# Вспомогательная функция установки состояния
# ------------------------------------------------
async def set_user_state(peer_id: int, state: str):
    storage.set(f"state_{peer_id}", state)

async def get_user_state(peer_id: int) -> str:
    return storage.get(f"state_{peer_id}")

async def clear_user_state(peer_id: int):
    storage.delete(f"state_{peer_id}")

# ------------------------------------------------
# /start и главное меню
# ------------------------------------------------
@bot.on.message(text="/start")
@bot.on.message(text="Главное меню")
async def start(message: Message):
    await clear_user_state(message.peer_id)
    await message.answer(
        "👋 Добро пожаловать! Я помощник для записи на занятия.\n"
        "Выберите действие:",
        keyboard=MAIN_KEYBOARD
    )

# ------------------------------------------------
# Записаться
# ------------------------------------------------
@bot.on.message(text="📝 Записаться")
async def booking_start(message: Message):
    tutors = await core.get_tutors_with_subjects()
    if not tutors:
        await message.answer("Нет доступных преподавателей.")
        return

    keyboard = Keyboard(inline=True)
    for tid, tdata in tutors.items():
        keyboard.add(Callback(tdata["name"], payload={"action": "choose_tutor", "tutor_id": str(tid)}))
    keyboard.add(Callback("🔙 Отмена", payload={"action": "cancel"}))
    await message.answer("Выберите преподавателя:", keyboard=keyboard)
    await set_user_state(message.peer_id, BookingStates.CHOOSING_TUTOR)

# ------------------------------------------------
# Универсальный обработчик callback'ов
# ------------------------------------------------
@bot.on.raw_event()
async def callback_handler(event: dict):
    if event["type"] != "message_event":
        return
    payload = event["object"]["payload"]
    peer_id = event["object"]["peer_id"]
    user_id = event["object"]["user_id"]
    current_state = await get_user_state(peer_id)

    # --- Отмена ---
    if payload.get("action") == "cancel":
        await bot.api.messages.send_message_event_answer(
            event_id=event["object"]["event_id"],
            user_id=user_id,
            peer_id=peer_id,
            event_data={"type": "show_snackbar", "text": "Действие отменено"}
        )
        await clear_user_state(peer_id)
        await bot.api.messages.send(peer_id=peer_id, message="Главное меню", keyboard=MAIN_KEYBOARD)
        return

    # --- Выбор преподавателя ---
    if current_state == BookingStates.CHOOSING_TUTOR and payload.get("action") == "choose_tutor":
        tutor_id = int(payload["tutor_id"])
        tutors = await core.get_tutors_with_subjects()
        tutor = tutors.get(tutor_id)
        if not tutor:
            await bot.api.messages.send_message_event_answer(...)
            return
        storage.set(f"bk_tutor_id_{peer_id}", tutor_id)
        storage.set(f"bk_tutor_name_{peer_id}", tutor["name"])
        subjects = list(tutor["subjects"].keys())
        if len(subjects) == 1:
            storage.set(f"bk_subject_{peer_id}", subjects[0])
            await show_date_selection(peer_id)
            return
        keyboard = Keyboard(inline=True)
        for subj in subjects:
            keyboard.add(Callback(subj, payload={"action": "choose_subject", "subject": subj}))
        keyboard.add(Callback("🔙 Назад", payload={"action": "back_to_tutors"}))
        await bot.api.messages.send_message_event_answer(
            event_id=event["object"]["event_id"],
            user_id=user_id,
            peer_id=peer_id,
            event_data={"type": "show_snackbar", "text": "Выберите предмет"}
        )
        await bot.api.messages.send(peer_id=peer_id, message="Выберите предмет:", keyboard=keyboard)
        await set_user_state(peer_id, BookingStates.CHOOSING_SUBJECT)
        return

    # --- Выбор предмета ---
    if current_state == BookingStates.CHOOSING_SUBJECT and payload.get("action") == "choose_subject":
        subject = payload["subject"]
        storage.set(f"bk_subject_{peer_id}", subject)
        await show_date_selection(peer_id)
        return

    # --- Назад к преподавателям ---
    if payload.get("action") == "back_to_tutors":
        await booking_start(await bot.api.messages.send(peer_id=peer_id, message="", keyboard=MAIN_KEYBOARD))
        return

    # --- Выбор даты ---
    if current_state == BookingStates.CHOOSING_DATE and payload.get("action") == "choose_date":
        date_str = payload["date"]
        storage.set(f"bk_date_{peer_id}", date_str)
        tutor_id = storage.get(f"bk_tutor_id_{peer_id}")
        slots = await core.get_available_slots(tutor_id, date_str)
        if not slots:
            await bot.api.messages.send_message_event_answer(...)
            await bot.api.messages.send(peer_id=peer_id, message="На эту дату нет свободного времени.")
            return
        keyboard = Keyboard(inline=True)
        for slot in slots:
            keyboard.add(Callback(slot, payload={"action": "choose_time", "time": slot}))
        keyboard.add(Callback("🔙 К датам", payload={"action": "back_to_dates"}))
        await bot.api.messages.send(peer_id=peer_id, message="Выберите время:", keyboard=keyboard)
        await set_user_state(peer_id, BookingStates.CHOOSING_TIME)
        return

    # --- Выбор времени ---
    if current_state == BookingStates.CHOOSING_TIME and payload.get("action") == "choose_time":
        time_slot = payload["time"]
        storage.set(f"bk_time_{peer_id}", time_slot)
        tutor_name = storage.get(f"bk_tutor_name_{peer_id}")
        subject = storage.get(f"bk_subject_{peer_id}")
        date_str = storage.get(f"bk_date_{peer_id}")
        text = (
            f"📋 Проверьте данные:\n"
            f"👨‍🏫 {tutor_name}\n📚 {subject}\n📅 {date_str}\n🕒 {time_slot}\n\nПодтвердить?"
        )
        keyboard = Keyboard(inline=True)
        keyboard.add(Callback("✅ Подтвердить", payload={"action": "confirm_booking"}))
        keyboard.add(Callback("🔙 Изменить время", payload={"action": "back_to_dates"}))
        keyboard.add(Callback("❌ Отмена", payload={"action": "cancel"}))
        await bot.api.messages.send(peer_id=peer_id, message=text, keyboard=keyboard)
        await set_user_state(peer_id, BookingStates.CONFIRMATION)
        return

    # --- Подтверждение брони ---
    if current_state == BookingStates.CONFIRMATION and payload.get("action") == "confirm_booking":
        tutor_id = storage.get(f"bk_tutor_id_{peer_id}")
        subject = storage.get(f"bk_subject_{peer_id}")
        date_str = storage.get(f"bk_date_{peer_id}")
        time_slot = storage.get(f"bk_time_{peer_id}")
        username = f"vk{user_id}"
        await core.create_booking(tutor_id, user_id, username, subject, date_str, time_slot)
        await bot.api.messages.send(peer_id=peer_id, message="✅ Заявка отправлена преподавателю.", keyboard=MAIN_KEYBOARD)
        await clear_user_state(peer_id)
        return

    # --- Назад к датам ---
    if payload.get("action") == "back_to_dates":
        await show_date_selection(peer_id)
        return

# --- Функция показа дат ---
async def show_date_selection(peer_id: int):
    tutor_id = storage.get(f"bk_tutor_id_{peer_id}")
    dates = await core.get_available_dates(tutor_id, days_ahead=14)
    if not dates:
        await bot.api.messages.send(peer_id=peer_id, message="Нет доступных дат.", keyboard=MAIN_KEYBOARD)
        await clear_user_state(peer_id)
        return
    keyboard = Keyboard(inline=True)
    for date_str in dates:
        dt = datetime.strptime(date_str, "%d.%m.%Y")
        day_name = core.WEEKDAY_NAMES[core.WEEKDAYS[dt.weekday()]]
        label = f"{date_str} ({day_name})"
        keyboard.add(Callback(label, payload={"action": "choose_date", "date": date_str}))
    keyboard.add(Callback("🔙 Назад", payload={"action": "back_to_tutors"}))
    await bot.api.messages.send(peer_id=peer_id, message="Выберите дату:", keyboard=keyboard)
    await set_user_state(peer_id, BookingStates.CHOOSING_DATE)

# ------------------------------------------------
# Мои записи
# ------------------------------------------------
@bot.on.message(text="📋 Мои записи")
async def my_bookings(message: Message):
    user_id = message.from_id
    bookings = await core.get_student_bookings(user_id)
    if not bookings:
        await message.answer("У вас нет активных записей.")
        return
    keyboard = Keyboard(inline=True)
    for b in bookings:
        status = "⏳" if b["status"] == "pending" else "✅"
        btn_text = f"{status} {b['subject']} {b['date']} {b['time_slot']}"
        keyboard.add(Callback(btn_text, payload={"action": "booking_info", "booking_id": str(b["id"])}))
    keyboard.add(Callback("🔙 Назад", payload={"action": "back_to_menu"}))
    await message.answer("Ваши записи:", keyboard=keyboard)

@bot.on.raw_event()
async def booking_info_handler(event: dict):
    if event["type"] != "message_event":
        return
    payload = event["object"]["payload"]
    if payload.get("action") != "booking_info":
        return
    booking_id = int(payload["booking_id"])
    from database import get_all_bookings
    bookings = await get_all_bookings()
    booking = bookings.get(booking_id)
    if not booking or booking["user_id"] != event["object"]["user_id"]:
        return
    can_cancel = False
    if booking["status"] in ("pending", "confirmed"):
        dt = datetime.strptime(booking["date"] + " " + booking["time_slot"].split("-")[0].replace(".", ":"), "%d.%m.%Y %H:%M")
        if (dt - datetime.now()) > timedelta(hours=24):
            can_cancel = True
    tutor_name = (await core.get_tutors_with_subjects()).get(booking["tutor_id"], {}).get("name", "Неизвестный")
    text = f"👨‍🏫 {tutor_name}\n📚 {booking['subject']}\n📅 {booking['date']} {booking['time_slot']}\nСтатус: {'подтверждено' if booking['status']=='confirmed' else 'ожидает'}"
    keyboard = Keyboard(inline=True)
    if can_cancel:
        keyboard.add(Callback("❌ Отменить запись", payload={"action": "cancel_booking", "booking_id": str(booking_id)}))
    keyboard.add(Callback("🔙 К списку", payload={"action": "my_bookings"}))
    await bot.api.messages.send_message_event_answer(event_id=event["object"]["event_id"], user_id=event["object"]["user_id"], peer_id=event["object"]["peer_id"])
    await bot.api.messages.send(peer_id=event["object"]["peer_id"], message=text, keyboard=keyboard)

@bot.on.raw_event()
async def cancel_booking_handler(event: dict):
    if event["type"] != "message_event":
        return
    payload = event["object"]["payload"]
    if payload.get("action") != "cancel_booking":
        return
    booking_id = int(payload["booking_id"])
    success = await core.cancel_booking(booking_id)
    if success:
        await bot.api.messages.send(peer_id=event["object"]["peer_id"], message="✅ Запись отменена.", keyboard=MAIN_KEYBOARD)
    else:
        await bot.api.messages.send(peer_id=event["object"]["peer_id"], message="❌ Не удалось отменить.")

# ------------------------------------------------
# Помощь
# ------------------------------------------------
@bot.on.message(text="❓ Помощь")
async def help_cmd(message: Message):
    await message.answer("📌 Помощь по боту... (текст справки)")

# ------------------------------------------------
# Запуск
# ------------------------------------------------
async def main():
    from database import init_db, close_db
    await init_db()
    try:
        await bot.run()
    finally:
        await close_db()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
