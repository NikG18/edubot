# vk_bot.py
import logging
import asyncio
from database import get_all_bookings
from datetime import datetime, timedelta
from vkbottle import Bot, Keyboard, KeyboardButtonColor, Text, Callback
from vkbottle.bot import Message
from vkbottle_types.events.bot import CallbackEvent          
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

# ------------------------------------------------
# FSM состояния для записи ученика
# ------------------------------------------------
class BookingStates(StatesGroup):
    CHOOSING_TUTOR = State()
    CHOOSING_SUBJECT = State()
    CHOOSING_DATE = State()
    CHOOSING_TIME = State()
    CONFIRMATION = State()

# ------------------------------------------------
# Главное меню (клавиатура для ученика)
# ------------------------------------------------
MAIN_KEYBOARD = (
    Keyboard(one_time=False, inline=False)
    .add(Text("📝 Записаться"), color=KeyboardButtonColor.POSITIVE)
    .add(Text("📋 Мои записи"), color=KeyboardButtonColor.PRIMARY)
    .row()
    .add(Text("❓ Помощь"), color=KeyboardButtonColor.SECONDARY)
)

# ------------------------------------------------
# /start и главное меню
# ------------------------------------------------
@bot.on.message(text="/start")
@bot.on.message(text="Главное меню")
async def start(message: Message):
    await message.answer(
        "👋 Добро пожаловать! Я помощник для записи на занятия.\n"
        "Выберите действие:",
        keyboard=MAIN_KEYBOARD
    )

# ------------------------------------------------
# Кнопка "Записаться"
# ------------------------------------------------
@bot.on.message(text="📝 Записаться")
async def booking_start(message: Message):
    tutors = await core.get_tutors_with_subjects()
    if not tutors:
        await message.answer("Нет доступных преподавателей.")
        return

    # Строим инлайн-клавиатуру со списком преподавателей
    keyboard = Keyboard(inline=True)
    for tid, tdata in tutors.items():
        # Callback содержит ID преподавателя
        keyboard.add(Callback(tdata["name"], payload={"action": "choose_tutor", "tutor_id": str(tid)}))
    keyboard.add(Callback("🔙 Отмена", payload={"action": "cancel"}))
    await message.answer("Выберите преподавателя:", keyboard=keyboard)
    await bot.state_dispenser.set(message.peer_id, BookingStates.CHOOSING_TUTOR)

# ------------------------------------------------
# Обработка выбора преподавателя (callback)
# ------------------------------------------------
@bot.on.raw_event(CallbackEvent, StateRule(BookingStates.CHOOSING_TUTOR))
async def tutor_chosen(event: CallbackEvent):
    payload = event.payload
    if payload.get("action") == "cancel":
        await event.answer("Действие отменено.", keyboard=MAIN_KEYBOARD)
        await bot.state_dispenser.delete(event.peer_id)
        return

    if payload.get("action") != "choose_tutor":
        await event.answer("Пожалуйста, выберите преподавателя из списка.")
        return

    tutor_id = int(payload["tutor_id"])
    tutors = await core.get_tutors_with_subjects()
    tutor = tutors.get(tutor_id)
    if not tutor:
        await event.answer("Преподаватель не найден.", keyboard=MAIN_KEYBOARD)
        await bot.state_dispenser.delete(event.peer_id)
        return

    # Сохраняем выбранного преподавателя
    storage.set(f"bk_tutor_id_{event.peer_id}", tutor_id)
    storage.set(f"bk_tutor_name_{event.peer_id}", tutor["name"])

    # Предметы преподавателя
    subjects = list(tutor["subjects"].keys())
    if len(subjects) == 1:
        # Если один предмет – сразу переходим к выбору даты
        storage.set(f"bk_subject_{event.peer_id}", subjects[0])
        await show_date_selection(event.peer_id)
        return

    # Строим клавиатуру с предметами
    keyboard = Keyboard(inline=True)
    for subj in subjects:
        keyboard.add(Callback(subj, payload={"action": "choose_subject", "subject": subj}))
    keyboard.add(Callback("🔙 Назад", payload={"action": "back_to_tutors"}))
    await event.answer("Выберите предмет:", keyboard=keyboard)
    await bot.state_dispenser.set(event.peer_id, BookingStates.CHOOSING_SUBJECT)

# ------------------------------------------------
# Выбор предмета (callback)
# ------------------------------------------------
@bot.on.raw_event(CallbackEvent, StateRule(BookingStates.CHOOSING_SUBJECT))
async def subject_chosen(event: CallbackEvent):
    payload = event.payload
    if payload.get("action") == "back_to_tutors":
        await booking_start(await event.answer("Возврат к списку преподавателей."))  # немного криво, но для примера
        return

    if payload.get("action") != "choose_subject":
        await event.answer("Пожалуйста, выберите предмет.")
        return

    subject = payload["subject"]
    storage.set(f"bk_subject_{event.peer_id}", subject)
    await show_date_selection(event.peer_id)

# ------------------------------------------------
# Показать выбор даты (общая функция)
# ------------------------------------------------
async def show_date_selection(peer_id: int):
    tutor_id = storage.get(f"bk_tutor_id_{peer_id}")
    available_dates = await core.get_available_dates(tutor_id, days_ahead=14)  # на 2 недели
    if not available_dates:
        await bot.api.messages.send(peer_id=peer_id,
                                    message="У преподавателя пока нет свободных дат.",
                                    keyboard=MAIN_KEYBOARD)
        await bot.state_dispenser.delete(peer_id)
        return

    keyboard = Keyboard(inline=True)
    for date_str in available_dates:
        dt = datetime.strptime(date_str, "%d.%m.%Y")
        day_name = core.WEEKDAY_NAMES.get(core.WEEKDAYS[dt.weekday()], "?")
        label = f"{date_str} ({day_name})"
        keyboard.add(Callback(label, payload={"action": "choose_date", "date": date_str}))
    keyboard.add(Callback("🔙 Назад к предметам", payload={"action": "back_to_subjects"}))
    await bot.api.messages.send(peer_id=peer_id, message="Выберите дату:", keyboard=keyboard)
    await bot.state_dispenser.set(peer_id, BookingStates.CHOOSING_DATE)

# ------------------------------------------------
# Выбор даты (callback)
# ------------------------------------------------
@bot.on.raw_event(CallbackEvent, StateRule(BookingStates.CHOOSING_DATE))
async def date_chosen(event: CallbackEvent):
    payload = event.payload
    if payload.get("action") == "back_to_subjects":
        # Вернуться к выбору предмета
        tutor_id = storage.get(f"bk_tutor_id_{event.peer_id}")
        tutors = await core.get_tutors_with_subjects()
        tutor = tutors.get(tutor_id)
        subjects = list(tutor["subjects"].keys())
        keyboard = Keyboard(inline=True)
        for subj in subjects:
            keyboard.add(Callback(subj, payload={"action": "choose_subject", "subject": subj}))
        keyboard.add(Callback("🔙 Назад", payload={"action": "back_to_tutors"}))
        await event.answer("Выберите предмет:", keyboard=keyboard)
        await bot.state_dispenser.set(event.peer_id, BookingStates.CHOOSING_SUBJECT)
        return

    if payload.get("action") != "choose_date":
        await event.answer("Пожалуйста, выберите дату.")
        return

    date_str = payload["date"]
    storage.set(f"bk_date_{event.peer_id}", date_str)

    # Получаем свободные слоты
    tutor_id = storage.get(f"bk_tutor_id_{event.peer_id}")
    slots = await core.get_available_slots(tutor_id, date_str)
    if not slots:
        await event.answer("На эту дату нет свободного времени. Выберите другую дату.", keyboard=...)
        # Вернуть клавиатуру выбора дат (можно перерисовать)
        return

    keyboard = Keyboard(inline=True)
    for slot in slots:
        keyboard.add(Callback(slot, payload={"action": "choose_time", "time": slot}))
    keyboard.add(Callback("🔙 К датам", payload={"action": "back_to_dates"}))
    await event.answer("Выберите время:", keyboard=keyboard)
    await bot.state_dispenser.set(event.peer_id, BookingStates.CHOOSING_TIME)

# ------------------------------------------------
# Выбор времени (callback)
# ------------------------------------------------
@bot.on.raw_event(CallbackEvent, StateRule(BookingStates.CHOOSING_TIME))
async def time_chosen(event: CallbackEvent):
    payload = event.payload
    if payload.get("action") == "back_to_dates":
        await show_date_selection(event.peer_id)
        return

    if payload.get("action") != "choose_time":
        await event.answer("Пожалуйста, выберите время.")
        return

    time_slot = payload["time"]
    storage.set(f"bk_time_{event.peer_id}", time_slot)

    # Собираем информацию для подтверждения
    tutor_name = storage.get(f"bk_tutor_name_{event.peer_id}")
    subject = storage.get(f"bk_subject_{event.peer_id}")
    date_str = storage.get(f"bk_date_{event.peer_id}")

    text = (
        f"📋 Проверьте данные:\n"
        f"👨‍🏫 Преподаватель: {tutor_name}\n"
        f"📚 Предмет: {subject}\n"
        f"📅 Дата: {date_str}\n"
        f"🕒 Время: {time_slot}\n\n"
        f"Подтвердить запись?"
    )
    keyboard = Keyboard(inline=True)
    keyboard.add(Callback("✅ Подтвердить", payload={"action": "confirm_booking"}))
    keyboard.add(Callback("🔙 Изменить время", payload={"action": "back_to_dates"}))
    keyboard.add(Callback("❌ Отмена", payload={"action": "cancel"}))
    await event.answer(text, keyboard=keyboard)
    await bot.state_dispenser.set(event.peer_id, BookingStates.CONFIRMATION)

# ------------------------------------------------
# Подтверждение записи (callback)
# ------------------------------------------------
@bot.on.raw_event(CallbackEvent, StateRule(BookingStates.CONFIRMATION))
async def confirm_booking_handler(event: CallbackEvent):
    payload = event.payload
    if payload.get("action") == "cancel":
        await event.answer("Запись отменена.", keyboard=MAIN_KEYBOARD)
        await bot.state_dispenser.delete(event.peer_id)
        return

    if payload.get("action") != "confirm_booking":
        # Возможно, нажали изменить время – обработаем отдельно
        if payload.get("action") == "back_to_dates":
            await show_date_selection(event.peer_id)
            return
        await event.answer("Пожалуйста, подтвердите или отмените.")
        return

    peer_id = event.peer_id
    tutor_id = storage.get(f"bk_tutor_id_{peer_id}")
    subject = storage.get(f"bk_subject_{peer_id}")
    date_str = storage.get(f"bk_date_{peer_id}")
    time_slot = storage.get(f"bk_time_{peer_id}")
    username = f"vk{event.user_id}"  # идентификатор ученика (можно использовать vk_id)

    # Создаём бронирование
    booking_id = await core.create_booking(
        tutor_id=tutor_id,
        user_id=event.user_id,      # ID пользователя VK
        username=username,
        subject=subject,
        date_str=date_str,
        time_slot=time_slot
    )

    # Уведомление ученику
    await event.answer("✅ Заявка отправлена преподавателю. Ожидайте подтверждения.", keyboard=MAIN_KEYBOARD)

    # Уведомление преподавателю через Telegram (уже реализовано в Telegram-боте, но мы можем повторить логику здесь? Лучше не дублировать.
    # Пока преподаватель увидит заявку в Telegram-боте, т.к. используется общая база. Telegram-бот должен проверять новые бронирования и уведомлять.
    # Мы можем отправить уведомление через самого себя (вызвать метод API Telegram), но проще добавить в core функцию уведомления,
    # которая отправляет сообщение преподавателю, но для этого нужен объект бота Telegram. Временно оставим без немедленного уведомления.
    # Альтернатива: преподаватели сами заходят в панель Telegram-бота и видят заявки.

    await bot.state_dispenser.delete(peer_id)

# ------------------------------------------------
# Кнопка "Мои записи"
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
        date_str = b["date"]
        time_slot = b["time_slot"]
        status = "⏳" if b["status"] == "pending" else "✅"
        btn_text = f"{status} {b['subject']} {date_str} {time_slot}"
        keyboard.add(Callback(btn_text, payload={"action": "booking_info", "booking_id": str(b["id"])}))
    keyboard.add(Callback("🔙 Назад", payload={"action": "back_to_menu"}))
    await message.answer("Ваши записи:", keyboard=keyboard)

# Обработчик нажатия на запись (показать детали и кнопку отмены)
@bot.on.raw_event(CallbackEvent)
async def booking_info_handler(event: CallbackEvent):
    payload = event.payload
    if payload.get("action") != "booking_info":
        return

    booking_id = int(payload["booking_id"])
    # Получаем информацию о бронировании из базы (через core или прямой запрос)
    # В core у нас нет функции get_booking_by_id, добавим или используем database напрямую
    bookings = await get_all_bookings()
    booking = bookings.get(booking_id)
    if not booking or booking["user_id"] != event.user_id:
        await event.answer("Запись не найдена.")
        return

    can_cancel = False
    if booking["status"] in ("pending", "confirmed"):
        date_str = booking["date"]
        time_part = booking["time_slot"].split("-")[0].replace(".", ":")
        dt = datetime.strptime(f"{date_str} {time_part}", "%d.%m.%Y %H:%M")
        if (dt - datetime.now()) > timedelta(hours=24):
            can_cancel = True

    tutor_name = (await core.get_tutors_with_subjects()).get(booking["tutor_id"], {}).get("name", "Неизвестный")
    text = (
        f"👨‍🏫 {tutor_name}\n"
        f"📚 {booking['subject']}\n"
        f"📅 {booking['date']} {booking['time_slot']}\n"
        f"Статус: {'подтверждено' if booking['status']=='confirmed' else 'ожидает'}"
    )
    keyboard = Keyboard(inline=True)
    if can_cancel:
        keyboard.add(Callback("❌ Отменить запись", payload={"action": "cancel_booking", "booking_id": str(booking_id)}))
    keyboard.add(Callback("🔙 К списку", payload={"action": "my_bookings"}))
    await event.answer(text, keyboard=keyboard)

# Обработчик отмены бронирования
@bot.on.raw_event(CallbackEvent)
async def cancel_booking_handler(event: CallbackEvent):
    payload = event.payload
    if payload.get("action") != "cancel_booking":
        return
    booking_id = int(payload["booking_id"])
    success = await core.cancel_booking(booking_id)
    if success:
        await event.answer("✅ Запись отменена.", keyboard=MAIN_KEYBOARD)
    else:
        await event.answer("❌ Не удалось отменить (возможно, осталось менее 24 часов).")

# ------------------------------------------------
# Кнопка "Помощь"
# ------------------------------------------------
@bot.on.message(text="❓ Помощь")
async def help_cmd(message: Message):
    help_text = (
        "📌 <b>Помощь</b>\n"
        "• Записаться на занятие: выберите преподавателя, предмет, дату и время.\n"
        "• Мои записи: просмотр и отмена активных записей.\n"
        "• Подтверждение занятий происходит через Telegram-бота преподавателя.\n"
        "• Вопросы можно задать администратору через Telegram-бота @Ganzhaedubot."
    )
    await message.answer(help_text)

# ------------------------------------------------
# Обработчик кнопки "Назад в меню" (callback)
# ------------------------------------------------
@bot.on.raw_event(CallbackEvent)
async def back_to_menu(event: CallbackEvent):
    if event.payload.get("action") == "back_to_menu":
        await event.answer("Главное меню", keyboard=MAIN_KEYBOARD)

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
