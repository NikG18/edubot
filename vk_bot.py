import asyncio
import logging
import sys
import re
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Union

from vkbottle import (
    Bot, Keyboard, KeyboardButtonColor, Text, OpenLink,
    BaseStateGroup, BuiltinStateDispenser
)
from vkbottle.bot import Message, MessageEvent
from vkbottle.bot import rules

# Импорт всех функций из базы данных
from database import (
    init_db, get_all_tutors, add_tutor, update_tutor, delete_tutor,
    add_subject, update_subject, delete_subject,
    get_schedule, add_schedule_slot, delete_schedule_slot,
    get_all_bookings, add_booking, update_booking, delete_booking,
    get_tutor_by_telegram_id, get_student_subscriptions, get_tutor_financials, get_all_tutors_stats,
    get_students_stats, get_all_tutors_stats_by_month, get_students_stats_by_month,
    block_day, unblock_day, is_day_blocked, recalculate_monthly_stats,
    get_user_email, set_user_email,
    add_lesson_to_balance, calculate_auto_commission,
    set_pending_email_request, get_pending_email_request, delete_pending_email_request,
    close_db, cleanup_old_bookings, WEEKDAYS, WEEKDAY_NAMES, get_tutor_by_vk_id
)
from payments import create_payment, check_payment

# -------------------- Конфигурация --------------------
ADMIN_VK_ID = int(os.environ.get("ADMIN_VK_ID", 0))
BOT_TOKEN = os.environ.get("VK_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("VK_BOT_TOKEN не задан!")

TINKOFF_TERMINAL_KEY = os.environ.get("TINKOFF_TERMINAL_KEY", "")
TINKOFF_SECRET_KEY = os.environ.get("TINKOFF_SECRET_KEY", "")


# -------------------- Бот и диспетчер состояний --------------------
bot = Bot(token=BOT_TOKEN)
state_dispenser = BuiltinStateDispenser()

# -------------------- Группы состояний (FSM) --------------------
class BookingStates(BaseStateGroup):
    choosing_tutor = "choosing_tutor"
    choosing_subject = "choosing_subject"
    waiting_date = "waiting_date"
    waiting_time = "waiting_time"
    waiting_confirmation = "waiting_confirmation"

class TrialBookingStates(BaseStateGroup):
    choosing_subject = "choosing_subject"
    waiting_date = "waiting_date"
    waiting_time = "waiting_time"
    waiting_confirmation = "waiting_confirmation"

class ContactStates(BaseStateGroup):
    choosing_tutor = "choosing_tutor"
    waiting_message = "waiting_message"
    waiting_reply = "waiting_reply"

class AdminStates(BaseStateGroup):
    waiting_commission = "waiting_commission"
    waiting_name = "waiting_name"
    waiting_photo = "waiting_photo"
    waiting_description = "waiting_description"
    waiting_telegram_id = "waiting_telegram_id"
    waiting_vk_id = "waiting_vk_id"   # здесь VK ID
    waiting_subject_name = "waiting_subject_name"
    waiting_subject_price = "waiting_subject_price"
    waiting_edit_choice = "waiting_edit_choice"
    waiting_new_value = "waiting_new_value"
    waiting_delete_confirm = "waiting_delete_confirm"
    managing_subjects = "managing_subjects"
    adding_subject_name = "adding_subject_name"
    adding_subject_price = "adding_subject_price"
    editing_subject_choice = "editing_subject_choice"
    editing_subject_name_state = "editing_subject_name_state"
    editing_subject_price_state = "editing_subject_price_state"
    deleting_subject_confirm = "deleting_subject_confirm"
    waiting_inn = "waiting_inn"

class TutorScheduleStates(BaseStateGroup):
    choose_day = "choose_day"
    manage_day_slots = "manage_day_slots"
    add_slot = "add_slot"
    add_range = "add_range"
    delete_slot = "delete_slot"
    range_duration = "range_duration"
    range_break = "range_break"

class TutorContactStudentStates(BaseStateGroup):
    choosing_student = "choosing_student"
    waiting_message = "waiting_message"

class SupportUserStates(BaseStateGroup):
    waiting_message = "waiting_message"

class SupportAdminReplyStates(BaseStateGroup):
    waiting_reply = "waiting_reply"

class StudentRescheduleStates(BaseStateGroup):
    waiting_date = "waiting_date"
    waiting_time = "waiting_time"
    waiting_confirmation = "waiting_confirmation"

class TutorRescheduleStates(BaseStateGroup):
    waiting_date = "waiting_date"
    waiting_time = "waiting_time"
    waiting_confirmation = "waiting_confirmation"

class PaymentStates(BaseStateGroup):
    waiting_email = "waiting_email"

# -------------------- Клавиатуры главного меню --------------------
async def get_main_menu(user_id: int) -> str:
    """Возвращает JSON главной клавиатуры в зависимости от роли."""
    is_tutor = await get_tutor_by_vk_id(user_id) is not None
    is_admin = (user_id == ADMIN_VK_ID)

    kb = Keyboard(inline=False, one_time=False)
    if is_admin:
        kb.add(Text("ℹ️ Информация о репетиторах"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("📚 Информация о занятиях"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("📝 Запись на занятие"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("📋 Мои записи"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("💳 Оплата"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("📖 Учебные материалы"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("✉️ Связь с преподавателем"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("❓ Помощь"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("👨‍🏫 Админ-панель"), color=KeyboardButtonColor.POSITIVE)
        return kb.get_json()

    if is_tutor:
        kb.add(Text("📚 Информация о занятиях"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("📖 Учебные материалы"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("👨‍🏫 Панель преподавателя"), color=KeyboardButtonColor.POSITIVE)
        kb.row()
        kb.add(Text("✉️ Связь с учеником"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("🆘 Поддержка"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("❓ Помощь"), color=KeyboardButtonColor.PRIMARY)
        return kb.get_json()

    # Ученик (обычный пользователь)
    kb.add(Text("ℹ️ Информация о репетиторах"), color=KeyboardButtonColor.PRIMARY)
    kb.row()
    kb.add(Text("📚 Информация о занятиях"), color=KeyboardButtonColor.PRIMARY)
    kb.row()
    kb.add(Text("📝 Запись на занятие"), color=KeyboardButtonColor.PRIMARY)
    kb.row()
    kb.add(Text("📋 Мои записи"), color=KeyboardButtonColor.PRIMARY)
    kb.row()
    kb.add(Text("💳 Оплата"), color=KeyboardButtonColor.PRIMARY)
    kb.row()
    kb.add(Text("✉️ Связь с преподавателем"), color=KeyboardButtonColor.PRIMARY)
    kb.row()
    kb.add(Text("🆘 Поддержка"), color=KeyboardButtonColor.PRIMARY)
    kb.row()
    kb.add(Text("❓ Помощь"), color=KeyboardButtonColor.PRIMARY)
    return kb.get_json()

# -------------------- Вспомогательные функции --------------------
async def make_tutors_keyboard(callback_prefix: str, back_callback: str = "back_to_menu") -> str:
    tutors = await get_all_tutors()
    kb = Keyboard(inline=True)
    for tid, tdata in tutors.items():
        kb.add(Text(tdata["name"], payload={"cmd": f"{callback_prefix}_{tid}"}))
        kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": back_callback}))
    return kb.get_json()

async def make_subjects_keyboard(tutor_id: int, back_callback: str = "back_to_menu") -> str:
    tutors = await get_all_tutors()
    tutor = tutors.get(tutor_id)
    if not tutor:
        kb = Keyboard(inline=True)
        kb.add(Text("🔙 Назад", payload={"cmd": back_callback}))
        return kb.get_json()
    kb = Keyboard(inline=True)
    for subj in tutor["subjects"]:
        kb.add(Text(subj, payload={"cmd": f"subject_{tutor_id}_{subj}"}))
        kb.row()
    kb.add(Text("🔙 Назад к репетиторам", payload={"cmd": back_callback}))
    return kb.get_json()

async def get_available_slots(tutor_id: int, date_str: str, exclude_booking_id: int = None) -> list:
    """Свободные слоты на дату. Можно исключить конкретное бронирование (при переносе)."""
    date = datetime.strptime(date_str, "%d.%m.%Y")
    day_name = WEEKDAYS[date.weekday()]
    schedule = await get_schedule(tutor_id)
    if day_name not in schedule:
        return []
    all_slots = schedule[day_name]
    busy = []
    bookings = await get_all_bookings()
    for bid, b in bookings.items():
        if b["tutor_id"] == tutor_id and b["date"] == date_str and b["status"] in ("pending", "confirmed"):
            if exclude_booking_id and bid == exclude_booking_id:
                continue
            busy.append(b["time_slot"])
    free = [s for s in all_slots if s not in busy]
    if await is_day_blocked(tutor_id, day_name):
        return []
    return free

async def get_available_dates(tutor_id: int, days_ahead=30) -> list:
    today = datetime.now()
    available = []
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        date_str = d.strftime("%d.%m.%Y")
        free = await get_available_slots(tutor_id, date_str)
        if free:
            available.append(date_str)
    return available

def clean_time_input(user_input: str) -> str:
    cleaned = user_input.strip()
    cleaned = re.sub(r'[^\d:]', ':', cleaned)
    cleaned = re.sub(r':{2,}', ':', cleaned)
    cleaned = cleaned.strip(':')
    return cleaned

def split_into_slots(start_time: str, end_time: str, duration_min=90, break_min=0):
    fmt = "%H:%M"
    start = datetime.strptime(start_time, fmt)
    end = datetime.strptime(end_time, fmt)
    if end <= start:
        return []
    slots = []
    current = start
    while current + timedelta(minutes=duration_min) <= end:
        slot_end = current + timedelta(minutes=duration_min)
        slots.append(f"{current.strftime(fmt)}-{slot_end.strftime(fmt)}")
        current = slot_end + timedelta(minutes=break_min)
    return slots

async def create_and_send_payment(source, booking, email, booking_id):
    """Создаёт платёж в Т-Банке и отправляет ученику ссылку на оплату."""
    bid = booking_id
    tutors = await get_all_tutors()
    tutor = tutors.get(booking["tutor_id"])
    if not tutor:
        return
    price_rub = tutor["subjects"].get(booking["subject"])
    if not price_rub:
        return
    amount_kop = price_rub * 100

    now = datetime.now()
    if tutor.get("commission_mode") == "auto":
        percent, _ = await calculate_auto_commission(booking["tutor_id"], now.year, now.month)
    else:
        percent = tutor.get("commission_percent", 25)

    description = f"Занятие: {booking['subject']} с {tutor['name']} {booking['date']} {booking['time_slot']}"
    payment_url, payment_id = await create_payment(
        booking_id=bid,
        amount_kop=amount_kop,
        description=description,
        tutor_id=booking["tutor_id"],
        tutor_name=tutor["name"],
        customer_email=email
    )
    if not payment_url:
        await bot.api.messages.send(user_id=booking["user_id"], message="Ошибка создания платежа. Обратитесь в поддержку.", random_id=0)
        return

    await update_booking(bid,
                         status="confirmed",
                         reminded=0,
                         amount=amount_kop,
                         commission_percent=percent,
                         tinkoff_payment_id=payment_id)

    student_msg = (
        f"✅ Занятие подтверждено! Для завершения записи оплатите {price_rub} руб.:\n"
        f"📚 {booking['subject']}\n📅 {booking['date']} {booking['time_slot']}"
    )
    pay_keyboard = Keyboard(inline=True)
    pay_keyboard.add(OpenLink("Оплатить", payment_url))
    await bot.api.messages.send(
        user_id=booking["user_id"],
        message=student_msg,
        keyboard=pay_keyboard.get_json(),
        random_id=0
    )

    if tutor.get("vk_id"):  # здесь хранится VK ID
        await bot.api.messages.send(
            user_id=tutor["vk_id"],
            message=f"✅ Занятие с {booking['username']} подтверждено. Ожидается оплата.",
            random_id=0
        )

# -------------------- Обработчики начала диалога и главного меню --------------------
@bot.on.private_message(text=["Начать", "/start", "start"])
async def start_handler(message: Message):
    user_id = message.from_id
    await message.answer("👋 Добро пожаловать! Выберите действие в меню.", keyboard=await get_main_menu(user_id))

@bot.on.private_message(text="🔙 Назад")
async def back_to_main_menu_button(message: Message):
    await message.answer("Главное меню", keyboard=await get_main_menu(message.from_id))

# -------------------- Универсальный обработчик для inline-кнопок «Назад в меню» --------------------

async def back_to_menu(event: MessageEvent):
    user_id = event.user_id
    # Сброс состояний (если требуется)
    await state_dispenser.delete(user_id)
    await event.edit_message("Главное меню", keyboard=await get_main_menu(user_id))

# ==================== ИНФОРМАЦИЯ О РЕПЕТИТОРАХ ====================
@bot.on.private_message(text="ℹ️ Информация о репетиторах")
async def info_repetitors(message: Message):
    await message.answer("Кто из репетиторов вас интересует?", keyboard=await make_tutors_keyboard("tutor_info"))


async def back_to_tutors(event: MessageEvent):
    await event.edit_message("Кто из репетиторов вас интересует?", keyboard=await make_tutors_keyboard("tutor_info"))


async def show_tutor_info(event: MessageEvent):
    tid = event.payload["tutor_id"]
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        await event.edit_message("Репетитор не найден.")
        return
    text = tutor["description"] + "\n\nПредметы и цены:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    keyboard = Keyboard(inline=True)
    keyboard.add(Text("🎓 Записаться на пробное занятие", payload={"cmd": "trials", "tutor_id": tid}))
    keyboard.row()
    keyboard.add(Text("🔙 Назад к списку", payload={"cmd": "back_to_tutors"}))
    await event.edit_message(text, keyboard=keyboard.get_json())

# ==================== ПРОБНОЕ ЗАНЯТИЕ ====================

async def start_trials_booking(event: MessageEvent):
    tid = event.payload["tutor_id"]
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        await event.edit_message("Репетитор не найден.")
        return

    await state_dispenser.set(event.user_id, TrialBookingStates.choosing_subject)
    await state_dispenser.update_data(event.user_id, tutor_id=tid, tutor_name=tutor["name"])

    subjects = list(tutor["subjects"].keys())
    if len(subjects) == 1:
        subject = subjects[0]
        await state_dispenser.update_data(event.user_id, subject=subject)
        await event.edit_message("Ищем доступные слоты на ближайшие 7 дней...")
        await show_trial_dates(event, tid)
        return

    keyboard = Keyboard(inline=True)
    for subj in subjects:
        keyboard.add(Text(subj, payload={"cmd": "trial_subject", "subject": subj}))
        keyboard.row()
    keyboard.add(Text("🔙 Отмена", payload={"cmd": "back_to_tutors"}))
    await event.edit_message("Выберите предмет для пробного занятия:", keyboard=keyboard.get_json())


async def trial_subject_chosen(event: MessageEvent):
    subject = event.payload["subject"]
    await state_dispenser.update_data(event.user_id, subject=subject)
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    await event.edit_message("Ищем доступные слоты на ближайшие 7 дней...")
    await show_trial_dates(event, tid)

async def show_trial_dates(event: MessageEvent, tid: int):
    available_dates = await get_available_dates(tid, days_ahead=7)
    if not available_dates:
        keyboard = Keyboard(inline=True)
        keyboard.add(Text("🔙 К анкете", payload={"cmd": "tutor_info", "tutor_id": tid}))
        await event.edit_message("На ближайшие 7 дней у репетитора нет свободных слотов.", keyboard=keyboard.get_json())
        return

    buttons = []
    for d in available_dates:
        dt = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt.weekday()]]})"
        buttons.append(Text(label, payload={"cmd": "trial_date", "date": d}))
    kb = Keyboard(inline=True)
    for btn in buttons:
        kb.add(btn)
        kb.row()
    kb.add(Text("🔙 К анкете", payload={"cmd": "tutor_info", "tutor_id": tid}))
    await event.edit_message("Выберите дату пробного занятия:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TrialBookingStates.waiting_date)


async def trial_date_chosen(event: MessageEvent):
    date_str = event.payload["date"]
    await state_dispenser.update_data(event.user_id, date=date_str)
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    slots = await get_available_slots(tid, date_str)
    if not slots:
        kb = Keyboard(inline=True)
        kb.add(Text("🔙 К выбору даты", payload={"cmd": "back_to_trial_dates"}))
        await event.edit_message("На эту дату нет свободного времени.", keyboard=kb.get_json())
        return

    kb = Keyboard(inline=True)
    for s in slots:
        kb.add(Text(s, payload={"cmd": "trial_slot", "slot": s}))
        kb.row()
    kb.add(Text("🔙 К выбору даты", payload={"cmd": "back_to_trial_dates"}))
    await event.edit_message("Выберите время:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TrialBookingStates.waiting_time)


async def back_to_trial_dates(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    await show_trial_dates(event, tid)


async def trial_slot_chosen(event: MessageEvent):
    slot = event.payload["slot"]
    await state_dispenser.update_data(event.user_id, time_slot=slot)
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    tutors = await get_all_tutors()
    tutor_name = tutors[tid]["name"]

    text = (
        f"🎓 <b>Пробное занятие</b>\n"
        f"👨‍🏫 Репетитор: {tutor_name}\n"
        f"📚 Предмет: {data['subject']}\n"
        f"📅 Дата: {data['date']}\n"
        f"🕒 Время: {slot}\n\nПодтвердить запись?"
    )
    keyboard = Keyboard(inline=True)
    keyboard.add(Text("✅ Подтвердить", payload={"cmd": "confirm_trial"}))
    keyboard.row()
    keyboard.add(Text("🔙 К выбору времени", payload={"cmd": "back_to_trial_dates"}))
    await event.edit_message(text, keyboard=keyboard.get_json())
    await state_dispenser.set(event.user_id, TrialBookingStates.waiting_confirmation)


async def confirm_trial_booking(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    subject = data["subject"]
    date = data["date"]
    slot = data["time_slot"]
    user = await bot.api.users.get(event.user_id)
    username = f"{user[0].first_name} {user[0].last_name}"
    uid = event.user_id

    new_id = await add_booking(tid, uid, username, subject, date, slot)

    booking_msg = (
        f"📝 Новая заявка на пробное занятие (ожидает подтверждения)\n"
        f"👤 Ученик: {username} (ID: {uid})\n"
        f"👨‍🏫 Репетитор: {data['tutor_name']}\n"
        f"📚 Предмет: {subject}\n"
        f"📅 Дата: {date}\n"
        f"🕒 Время: {slot}"
    )

    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if tutor and tutor.get("vk_id"):
        keyboard = Keyboard(inline=True)
        keyboard.add(Text("✅ Подтвердить", payload={"cmd": f"tutor_confirm_{new_id}"}))
        keyboard.add(Text("❌ Отклонить", payload={"cmd": f"tutor_reject_{new_id}"}))
        try:
            await bot.api.messages.send(
                user_id=tutor["vk_id"],
                message=booking_msg,
                keyboard=keyboard.get_json(),
                random_id=0
            )
        except Exception:
            pass

    await event.edit_message("✅ Заявка на пробное занятие отправлена преподавателю. Ожидайте подтверждения.")
    await bot.api.messages.send(
        user_id=event.user_id,
        message="Главное меню",
        keyboard=await get_main_menu(event.user_id),
        random_id=0
    )
    await state_dispenser.delete(event.user_id)

# ==================== ИНФОРМАЦИЯ О ЗАНЯТИЯХ ====================
@bot.on.private_message(text="📚 Информация о занятиях")
async def lesson_info(message: Message):
    user_id = message.from_id
    is_tutor = await get_tutor_by_vk_id(user_id) is not None
    is_admin = (user_id == ADMIN_VK_ID)

    text = TUTOR_INFO_TEXT if (is_tutor and not is_admin) else STUDENT_INFO_TEXT
    keyboard = Keyboard(inline=True)
    keyboard.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await message.answer(text, keyboard=keyboard.get_json())

# ... (здесь должны быть константы TUTOR_INFO_TEXT и STUDENT_INFO_TEXT, они те же, что в Telegram-версии)
TUTOR_INFO_TEXT = (
    "👨‍🏫 Информация для преподавателей\n\n"
    "📍 Где проходят занятия?\n"
    "Занятия проводятся онлайн на платформе Zoom или Яндекс Телемост.\n\n"
    "💰 Как проходит оплата?\n"
    "Ученики оплачивают занятия напрямую платформе. Мы удерживаем комиссию и перечисляем вам "
    "вознаграждение за вычетом комиссии два раза в месяц (12-го и 27-го числа).\n\n"
    "📈 Прогрессивная шкала комиссии:\n"
    "• 25% — 1-20 занятий в месяц\n"
    "• 20% — 21-40 занятий в месяц (доступно после 2 месяцев работы)\n"
    "• 15% — более 40 занятий в месяц (доступно после 4 месяцев работы)\n\n"
    "📝 Ваши задачи:\n"
    "— Подготовка и проведение занятий.\n"
    "— Обратная связь ученикам.\n"
    "— Выставление временных слотов.\n"
    "— Подтверждение записей\n\n"
    "🆘 Поддержка:\n"
    "Все административные вопросы решаются через поддержку в боте."
)
STUDENT_INFO_TEXT = (
    "📚 Информация о занятиях для учеников\n\n"
    "Занятия проводятся онлайн на платформе Zoom или Яндекс Телемост.\n"
    "Длительность занятия — 60 или 90 минут.\n\n"
    "🎫 Действующие скидки:\n"
    "• За приведение друга — скидка 10% на все занятия в течение 30 дней\n"
    "• При покупке абонемента на 12 занятий — скидка 5%\n"
    "• При единовременной оплате 24 занятий — скидка 10%\n"
    "• При единовременной оплате 36 занятий — скидка 20%\n"
    "• Скидка для семей, у которых у нас занимаются более 1 ребенка — 20%\n\n"
    "Скидка при покупке абонемента не суммируется с другими акциями.\n"
    "Скидки суммируются с учётом условий. Подробности уточняйте у администратора.\n"
    "Скидки актуальны до 30.09.2026.\n\n"
    "📅 Запись на занятие – через раздел «Запись на занятие».\n"
    "💳 Оплата – через раздел «Оплата».\n"
    "✉️ Вопросы – через «Связь с преподавателем» или «Поддержка»."
)

# ==================== ЗАПИСЬ НА ЗАНЯТИЕ ====================
@bot.on.private_message(text="📝 Запись на занятие")
async def zapis(message: Message):
    await message.answer("Кто из репетиторов вас интересует?", keyboard=await make_tutors_keyboard("tutor_booking", back_callback="back_to_menu"))
    await state_dispenser.delete(message.from_id)


async def choose_tutor_booking(event: MessageEvent):
    tid = event.payload["tutor_id"]
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        await event.edit_message("Ошибка выбора репетитора.")
        return
    await state_dispenser.set(event.user_id, BookingStates.choosing_subject)
    await state_dispenser.update_data(event.user_id, tutor_id=tid, tutor_name=tutor["name"])
    await event.edit_message("На занятие по какому предмету вы хотите записаться?",
                             keyboard=await make_subjects_keyboard(tid, back_callback="back_to_tutors_booking"))


async def back_to_tutors_booking(event: MessageEvent):
    await state_dispenser.delete(event.user_id)
    await event.edit_message("Кто из репетиторов вас интересует?",
                             keyboard=await make_tutors_keyboard("tutor_booking", back_callback="back_to_menu"))


async def subject_chosen(event: MessageEvent):
    # payload: {"cmd": "subject_<tid>_<subject>"}
    parts = event.payload["cmd"].split("_", 2)
    if len(parts) < 3:
        return
    tid = int(parts[1])
    subject = parts[2]
    await state_dispenser.update_data(event.user_id, subject=subject, tutor_id=tid)
    dates = await get_available_dates(tid)
    if not dates:
        kb = Keyboard(inline=True)
        kb.add(Text("🔙 Назад к репетиторам", payload={"cmd": "back_to_tutors_booking"}))
        await event.edit_message("У этого преподавателя пока нет свободных дат. Попробуйте позже или свяжитесь с преподавателем.",
                                 keyboard=kb.get_json())
        return

    kb = Keyboard(inline=True)
    for d in dates:
        dt = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt.weekday()]]})"
        kb.add(Text(label, payload={"cmd": f"date_{d}"}))
        kb.row()
    kb.add(Text("🔙 Назад к репетиторам", payload={"cmd": "back_to_tutors_booking"}))
    await event.edit_message("Выберите дату:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, BookingStates.waiting_date)


async def choose_date(event: MessageEvent):
    date_str = event.payload["cmd"].split("_", 1)[1]
    await state_dispenser.update_data(event.user_id, date=date_str)
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    slots = await get_available_slots(tid, date_str)
    if not slots:
        kb = Keyboard(inline=True)
        kb.add(Text("🔙 К выбору даты", payload={"cmd": "back_to_date"}))
        await event.edit_message("На эту дату нет свободного времени.", keyboard=kb.get_json())
        return

    kb = Keyboard(inline=True)
    for s in slots:
        kb.add(Text(s, payload={"cmd": f"slot_{s}"}))
        kb.row()
    kb.add(Text("🔙 К выбору даты", payload={"cmd": "back_to_date"}))
    await event.edit_message("Выберите время:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, BookingStates.waiting_time)


async def back_to_date(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data.get("tutor_id")
    if not tid:
        return
    dates = await get_available_dates(tid)
    kb = Keyboard(inline=True)
    for d in dates:
        dt = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt.weekday()]]})"
        kb.add(Text(label, payload={"cmd": f"date_{d}"}))
        kb.row()
    kb.add(Text("🔙 Назад к репетиторам", payload={"cmd": "back_to_tutors_booking"}))
    await event.edit_message("Выберите дату:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, BookingStates.waiting_date)


async def choose_slot(event: MessageEvent):
    slot = event.payload["cmd"].split("_", 1)[1]
    await state_dispenser.update_data(event.user_id, time_slot=slot)
    data = await state_dispenser.get(event.user_id)
    tid = data.get("tutor_id")
    # подтягиваем имя репетитора, если нет
    if "tutor_name" not in data and tid:
        tutors = await get_all_tutors()
        tutor = tutors.get(tid)
        if tutor:
            data["tutor_name"] = tutor["name"]
            await state_dispenser.update_data(event.user_id, tutor_name=tutor["name"])
    tutor_name = data.get("tutor_name", "Неизвестный")

    text = (f"Проверьте данные:\n"
            f"Репетитор: {tutor_name}\n"
            f"Предмет: {data['subject']}\n"
            f"Дата: {data['date']}\n"
            f"Время: {slot}\n\nВсё верно?")
    kb = Keyboard(inline=True)
    kb.add(Text("✅ Подтвердить запись", payload={"cmd": "confirm_booking"}))
    kb.row()
    kb.add(Text("✏️ Изменить время", payload={"cmd": "back_to_date"}))
    kb.row()
    kb.add(Text("❌ Отменить запись", payload={"cmd": "cancel_booking"}))
    await event.edit_message(text, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, BookingStates.waiting_confirmation)


async def confirm_booking(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    subject = data["subject"]
    date = data["date"]
    slot = data["time_slot"]
    user = await bot.api.users.get(event.user_id)
    username = f"{user[0].first_name} {user[0].last_name}"
    uid = event.user_id

    new_id = await add_booking(tid, uid, username, subject, date, slot)

    booking_msg = (
        f"📝 Новая заявка на занятие (ожидает подтверждения преподавателя)\n"
        f"👤 Ученик: {username} (ID: {uid})\n"
        f"👨‍🏫 Репетитор: {data['tutor_name']}\n"
        f"📚 Предмет: {subject}\n"
        f"📅 Дата: {date}\n"
        f"🕒 Время: {slot}"
    )

    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if tutor and tutor.get("vk_id"):
        kb = Keyboard(inline=True)
        kb.add(Text("✅ Подтвердить", payload={"cmd": f"tutor_confirm_{new_id}"}))
        kb.add(Text("❌ Отклонить", payload={"cmd": f"tutor_reject_{new_id}"}))
        try:
            await bot.api.messages.send(
                user_id=tutor["vk_id"],
                message=booking_msg,
                keyboard=kb.get_json(),
                random_id=0
            )
        except Exception:
            pass

    await event.edit_message("✅ Заявка отправлена преподавателю. Ожидайте подтверждения.")
    await bot.api.messages.send(
        user_id=event.user_id,
        message="Главное меню",
        keyboard=await get_main_menu(event.user_id),
        random_id=0
    )
    await state_dispenser.delete(event.user_id)


async def cancel_booking(event: MessageEvent):
    await event.edit_message("Запись отменена. Возвращаемся в главное меню.")
    await state_dispenser.delete(event.user_id)
    await bot.api.messages.send(
        user_id=event.user_id,
        message="Главное меню",
        keyboard=await get_main_menu(event.user_id),
        random_id=0
    )

# ==================== МОИ ЗАПИСИ (УЧЕНИК) ====================
@bot.on.private_message(text="📋 Мои записи")
async def my_records(message: Message):
    await state_dispenser.delete(message.from_id)
    user_id = message.from_id
    bookings = await get_all_bookings()
    user_bookings = []
    for bid, b in bookings.items():
        if b["user_id"] == user_id and b["status"] in ("pending", "confirmed"):
            user_bookings.append((bid, b))

    if not user_bookings:
        kb = Keyboard(inline=True)
        kb.add(Text("📊 Статистика", payload={"cmd": "student_stats"}))
        kb.row()
        kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
        await message.answer("У вас пока нет активных записей.", keyboard=kb.get_json())
        return

    text_lines = ["Ваши записи:\n"]
    kb = Keyboard(inline=True)
    tutors = await get_all_tutors()
    for bid, b in user_bookings:
        tutor = tutors.get(b["tutor_id"], {"name": "Неизвестный"})
        dt = datetime.strptime(b["date"] + " " + b["time_slot"].split("-")[0].replace(".", ":"), "%d.%m.%Y %H:%M")
        now = datetime.now()
        can_act = (dt - now) > timedelta(hours=24) and b["status"] == "confirmed"
        status_text = "(ожидает подтверждения)" if b["status"] == "pending" else "(подтверждено)"

        text_lines.append(
            f"👨‍🏫 {tutor['name']}\n📚 {b['subject']}\n📅 {b['date']} 🕒 {b['time_slot']} {status_text}\n"
            + ("✅ Можно отменить/перенести" if can_act else "⚠️ Действия невозможны")
        )
        if can_act:
            kb.add(Text(f"🔄 Перенести: {tutor['name']} {b['date']} {b['time_slot']}",
                        payload={"cmd": f"reschedule_student_{bid}"}))
            kb.row()
            kb.add(Text(f"❌ Отменить: {tutor['name']} {b['date']} {b['time_slot']}",
                        payload={"cmd": f"cancel_student_{bid}"}))
            kb.row()
    kb.add(Text("📊 Статистика", payload={"cmd": "student_stats"}))
    kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await message.answer("\n".join(text_lines), keyboard=kb.get_json())


async def cancel_student_booking(event: MessageEvent):
    bid = int(event.payload["cmd"].split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking:
        await event.edit_message("Запись не найдена.")
        return

    dt = datetime.strptime(booking["date"] + " " + booking["time_slot"].split("-")[0].replace(".", ":"), "%d.%m.%Y %H:%M")
    if (dt - datetime.now()) <= timedelta(hours=24):
        await event.edit_message("Слишком поздно отменять. Стоимость не возвращается.")
        return

    await update_booking(bid, status="cancelled")
    student_id = booking["user_id"]
    tutor_id = booking["tutor_id"]
    tutors = await get_all_tutors()
    tutor_name = tutors.get(tutor_id, {}).get("name", "Неизвестный")
    await bot.api.messages.send(user_id=student_id, message="✅ Вы отменили занятие.", random_id=0)
    if tutor_id and (tutor_tg := tutors.get(tutor_id, {}).get("vk_id")):
        msg_tutor = (
            f"❌ Ученик {booking['username']} отменил занятие:\n"
            f"📚 {booking['subject']}\n📅 {booking['date']} 🕒 {booking['time_slot']}"
        )
        try:
            await bot.api.messages.send(user_id=tutor_tg, message=msg_tutor, random_id=0)
        except:
            pass
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 К моим записям", payload={"cmd": "back_to_my_records"}))
    await event.edit_message("✅ Запись отменена.", keyboard=kb.get_json())


async def show_student_stats(event: MessageEvent):
    user_id = event.user_id
    bookings = await get_all_bookings()
    completed = sum(1 for b in bookings.values() if b["user_id"] == user_id and b["status"] == "completed")
    subs = await get_student_subscriptions(user_id)
    sub_text = ""
    tutors = await get_all_tutors()
    for s in subs:
        tutor_name = tutors.get(s["tutor_id"], {}).get("name", "Неизвестный")
        sub_text += f"• {tutor_name}: {s['subject']} — осталось {s['remaining_lessons']} из {s['total_lessons']}\n"
    if not sub_text:
        sub_text = "У вас нет активных абонементов.\n"
    text = (
        "📊 Ваша статистика\n\n"
        f"✅ Проведено занятий: {completed}\n"
        f"🎫 Абонементы:\n{sub_text}"
    )
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 К моим записям", payload={"cmd": "back_to_my_records"}))
    await event.edit_message(text, keyboard=kb.get_json())


async def back_to_my_records(event: MessageEvent):
    # Возврат к просмотру записей – повторно вызываем my_records логику
    # Но т.к. здесь event, делаем упрощённо – отправляем новое сообщение
    user_id = event.user_id
    bookings = await get_all_bookings()
    user_bookings = [(bid, b) for bid, b in bookings.items() if b["user_id"] == user_id and b["status"] in ("pending", "confirmed")]
    if not user_bookings:
        kb = Keyboard(inline=True)
        kb.add(Text("📊 Статистика", payload={"cmd": "student_stats"}))
        kb.row()
        kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
        await event.edit_message("У вас пока нет активных записей.", keyboard=kb.get_json())
        return
    text_lines = ["Ваши записи:\n"]
    kb = Keyboard(inline=True)
    tutors = await get_all_tutors()
    for bid, b in user_bookings:
        tutor = tutors.get(b["tutor_id"], {"name": "Неизвестный"})
        dt = datetime.strptime(b["date"] + " " + b["time_slot"].split("-")[0].replace(".", ":"), "%d.%m.%Y %H:%M")
        can_act = (dt - datetime.now()) > timedelta(hours=24) and b["status"] == "confirmed"
        status_text = "(ожидает подтверждения)" if b["status"] == "pending" else "(подтверждено)"
        text_lines.append(f"👨‍🏫 {tutor['name']}\n📚 {b['subject']}\n📅 {b['date']} 🕒 {b['time_slot']} {status_text}\n" + ("✅ Можно отменить/перенести" if can_act else "⚠️ Действия невозможны"))
        if can_act:
            kb.add(Text(f"🔄 Перенести: {tutor['name']} {b['date']} {b['time_slot']}", payload={"cmd": f"reschedule_student_{bid}"}))
            kb.row()
            kb.add(Text(f"❌ Отменить: {tutor['name']} {b['date']} {b['time_slot']}", payload={"cmd": f"cancel_student_{bid}"}))
            kb.row()
    kb.add(Text("📊 Статистика", payload={"cmd": "student_stats"}))
    kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await event.edit_message("\n".join(text_lines), keyboard=kb.get_json())

# ==================== ПЕРЕНОС УЧЕНИКОМ ====================

async def student_reschedule_start(event: MessageEvent):
    bid = int(event.payload["cmd"].split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking or booking["status"] != "confirmed":
        await event.edit_message("Запись недоступна для переноса.")
        return
    dt = datetime.strptime(booking["date"] + " " + booking["time_slot"].split("-")[0].replace(".", ":"), "%d.%m.%Y %H:%M")
    if (dt - datetime.now()) <= timedelta(hours=24):
        await event.edit_message("Перенос возможен не позднее чем за 24 часа.")
        return
    await state_dispenser.set(event.user_id, StudentRescheduleStates.waiting_date)
    await state_dispenser.update_data(event.user_id,
        old_booking_id=bid,
        tutor_id=booking["tutor_id"],
        subject=booking["subject"],
        old_date=booking["date"],
        old_time=booking["time_slot"],
        student_id=booking["user_id"],
        student_username=booking["username"]
    )
    dates = await get_available_dates(booking["tutor_id"])
    if not dates:
        await event.edit_message("У преподавателя нет свободных дат для переноса.")
        return
    kb = Keyboard(inline=True)
    for d in dates:
        dt_date = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt_date.weekday()]]})"
        kb.add(Text(label, payload={"cmd": f"reschedule_date_{d}"}))
        kb.row()
    kb.add(Text("🔙 Отмена", payload={"cmd": "back_to_menu"}))
    await event.edit_message("Выберите новую дату:", keyboard=kb.get_json())


async def student_reschedule_date(event: MessageEvent):
    date_str = event.payload["cmd"].split("reschedule_date_")[1]
    await state_dispenser.update_data(event.user_id, new_date=date_str)
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    old_bid = data["old_booking_id"]
    slots = await get_available_slots(tid, date_str, exclude_booking_id=old_bid)
    if not slots:
        kb = Keyboard(inline=True)
        kb.add(Text("🔙 К выбору даты", payload={"cmd": "back_to_reschedule_date"}))
        await event.edit_message("На эту дату нет свободных слотов.", keyboard=kb.get_json())
        return
    kb = Keyboard(inline=True)
    for s in slots:
        kb.add(Text(s, payload={"cmd": f"reschedule_slot_{s}"}))
        kb.row()
    kb.add(Text("🔙 К выбору даты", payload={"cmd": "back_to_reschedule_date"}))
    await event.edit_message("Выберите новое время:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, StudentRescheduleStates.waiting_time)


async def back_to_reschedule_date(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    dates = await get_available_dates(tid)
    kb = Keyboard(inline=True)
    for d in dates:
        dt_date = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt_date.weekday()]]})"
        kb.add(Text(label, payload={"cmd": f"reschedule_date_{d}"}))
        kb.row()
    kb.add(Text("🔙 Отмена", payload={"cmd": "back_to_menu"}))
    await event.edit_message("Выберите новую дату:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, StudentRescheduleStates.waiting_date)


async def student_reschedule_slot(event: MessageEvent):
    slot = event.payload["cmd"].split("reschedule_slot_")[1]
    await state_dispenser.update_data(event.user_id, new_time=slot)
    data = await state_dispenser.get(event.user_id)
    text = (
        f"Перенос занятия:\n"
        f"Репетитор: {data.get('tutor_name', '')}\n"
        f"Предмет: {data['subject']}\n"
        f"Старая дата/время: {data['old_date']} {data['old_time']}\n"
        f"Новая дата/время: {data['new_date']} {slot}\n\nПодтвердить перенос?"
    )
    kb = Keyboard(inline=True)
    kb.add(Text("✅ Подтвердить перенос", payload={"cmd": "confirm_student_reschedule"}))
    kb.row()
    kb.add(Text("🔙 Назад к выбору времени", payload={"cmd": "back_to_reschedule_date"}))
    await event.edit_message(text, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, StudentRescheduleStates.waiting_confirmation)


async def confirm_student_reschedule(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    old_bid = data["old_booking_id"]
    tid = data["tutor_id"]
    new_date = data["new_date"]
    new_time = data["new_time"]
    subject = data["subject"]
    student_id = data["student_id"]
    student_username = data["student_username"]

    await update_booking(old_bid, status="cancelled")
    new_id = await add_booking(tid, student_id, student_username, subject, new_date, new_time)

    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    tutor_name = tutor["name"] if tutor else "Неизвестный"
    tutor_tg = tutor.get("vk_id") if tutor else None

    notify_tutor = (
        f"🔄 Ученик {student_username} перенёс занятие.\n"
        f"Предмет: {subject}\n"
        f"Было: {data['old_date']} {data['old_time']}\n"
        f"Новая заявка: {new_date} {new_time} (ожидает подтверждения)"
    )
    if tutor_tg:
        kb = Keyboard(inline=True)
        kb.add(Text("✅ Подтвердить", payload={"cmd": f"tutor_confirm_{new_id}"}))
        kb.add(Text("❌ Отклонить", payload={"cmd": f"tutor_reject_{new_id}"}))
        try:
            await bot.api.messages.send(user_id=tutor_tg, message=notify_tutor, keyboard=kb.get_json(), random_id=0)
        except:
            pass

    await bot.api.messages.send(user_id=student_id,
                                message=f"✅ Заявка на перенос отправлена преподавателю. Новое время: {new_date} {new_time}.",
                                random_id=0)
    await event.edit_message("Перенос выполнен. Ожидайте подтверждения нового времени.")
    await bot.api.messages.send(user_id=event.user_id,
                                message="Главное меню",
                                keyboard=await get_main_menu(event.user_id),
                                random_id=0)
    await state_dispenser.delete(event.user_id)

# ==================== ОПЛАТА ====================
@bot.on.private_message(text="💳 Оплата")
async def oplata(message: Message):
    kb = Keyboard(inline=True)
    kb.add(Text("📱 Оплата по QR-коду", payload={"cmd": "qr"}))
    kb.row()
    kb.add(Text("💳 Оплата банковской картой", payload={"cmd": "card"}))
    kb.row()
    kb.add(Text("📲 Перевод СБП по номеру телефона", payload={"cmd": "sbp"}))
    kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await message.answer("Какой способ оплаты вам удобнее?", keyboard=kb.get_json())


async def back_to_pay(event: MessageEvent):
    kb = Keyboard(inline=True)
    kb.add(Text("📱 Оплата по QR-коду", payload={"cmd": "qr"}))
    kb.row()
    kb.add(Text("💳 Оплата банковской картой", payload={"cmd": "card"}))
    kb.row()
    kb.add(Text("📲 Перевод СБП по номеру телефона", payload={"cmd": "sbp"}))
    kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await event.edit_message("Какой способ оплаты вам удобнее?", keyboard=kb.get_json())

async def qr(event: MessageEvent):
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 Назад к списку", payload={"cmd": "back_to_pay"}))
    await event.edit_message("📱 Сканируйте QR-код для оплаты в приложении вашего банка", keyboard=kb.get_json())


async def card(event: MessageEvent):
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 Назад к списку", payload={"cmd": "back_to_pay"}))
    await event.edit_message("💳 Переходите по ссылке и следуйте дальнейшим инструкциям", keyboard=kb.get_json())


async def sbp(event: MessageEvent):
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 Назад к списку", payload={"cmd": "back_to_pay"}))
    await event.edit_message("📲 Перевод выполняйте, указывая предмет и дату занятия, по номеру +7(933)120-96-03 на Т-банк",
                             keyboard=kb.get_json())

# ==================== УЧЕБНЫЕ МАТЕРИАЛЫ ====================
@bot.on.private_message(text="📖 Учебные материалы")
async def material(message: Message):
    kb = Keyboard(inline=True)
    kb.add(Text("📘 Учебные пособия", payload={"cmd": "book"}))
    kb.row()
    kb.add(Text("🎥 Авторские видео", payload={"cmd": "vid"}))
    kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await message.answer("Вы ищете пособия или видео?", keyboard=kb.get_json())


async def back_to_mat(event: MessageEvent):
    kb = Keyboard(inline=True)
    kb.add(Text("📘 Учебные пособия", payload={"cmd": "book"}))
    kb.row()
    kb.add(Text("🎥 Авторские видео", payload={"cmd": "vid"}))
    kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await event.edit_message("Вы ищете пособия или видео?", keyboard=kb.get_json())


async def book(event: MessageEvent):
    kb = Keyboard(inline=True)
    kb.add(Text("🧪 Химия", payload={"cmd": "bookh"}))
    kb.row()
    kb.add(Text("⚛️ Физика", payload={"cmd": "bookf"}))
    kb.row()
    kb.add(Text("🔙 Назад к списку", payload={"cmd": "back_to_mat"}))
    await event.edit_message("📘 Учебники и таблицы", keyboard=kb.get_json())


async def vid(event: MessageEvent):
    kb = Keyboard(inline=True)
    kb.add(Text("🧪 Химия", payload={"cmd": "videh"}))
    kb.row()
    kb.add(Text("⚛️ Физика", payload={"cmd": "videf"}))
    kb.row()
    kb.add(Text("🔙 Назад к списку", payload={"cmd": "back_to_mat"}))
    await event.edit_message("🎥 Видеоматериалы (записи реакций и явлений)", keyboard=kb.get_json())


async def bookh(event: MessageEvent):
    await event.answer("Скоро здесь будут пособия по химии", notification_type="callback")


async def bookf(event: MessageEvent):
    await event.answer("Скоро здесь будут пособия по физике", notification_type="callback")


async def videh(event: MessageEvent):
    await event.answer("Скоро здесь будут видео по химии", notification_type="callback")


async def videf(event: MessageEvent):
    await event.answer("Скоро здесь будут видео по физике", notification_type="callback")

# ==================== СВЯЗЬ С ПРЕПОДАВАТЕЛЕМ ====================
@bot.on.private_message(text="✉️ Связь с преподавателем")
async def svyaz(message: Message):
    await state_dispenser.set(message.from_id, ContactStates.choosing_tutor)
    await message.answer("Выберите преподавателя, которому хотите написать:",
                         keyboard=await make_tutors_keyboard("msg_tutor", back_callback="back_to_menu"))


async def choose_msg_tutor(event: MessageEvent):
    tid = int(event.payload["cmd"].split("_")[-1])
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        await event.edit_message("Преподаватель не найден.")
        return
    await state_dispenser.update_data(event.user_id, msg_tutor_id=tid, msg_tutor_name=tutor["name"])
    kb = Keyboard(inline=True)
    kb.add(Text("❌ Отмена", payload={"cmd": "cancel_msg_to_tutor"}))
    await event.edit_message(f"Вы пишете преподавателю {tutor['name']}.\nВведите ваше сообщение:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, ContactStates.waiting_message)


async def cancel_msg_to_tutor(event: MessageEvent):
    await state_dispenser.delete(event.user_id)
    kb = Keyboard(inline=True)
    tutors = await get_all_tutors()
    for tid, tdata in tutors.items():
        kb.add(Text(tdata["name"], payload={"cmd": f"msg_tutor_{tid}"}))
        kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await event.edit_message("Выберите преподавателя, которому хотите написать:", keyboard=kb.get_json())

@bot.on.private_message(state=ContactStates.waiting_message)
async def send_message_to_tutor(message: Message):
    user = await bot.api.users.get(message.from_id)
    username = f"{user[0].first_name} {user[0].last_name}" if user else str(message.from_id)
    data = await state_dispenser.get(message.from_id)
    tid = data["msg_tutor_id"]
    tutor_name = data["msg_tutor_name"]
    text = message.text.strip()

    forward_msg = (
        f"📨 Сообщение от ученика\n"
        f"👤 {username} (ID: {message.from_id})\n"
        f"✉️ Преподавателю: {tutor_name}\n\n"
        f"💬 Текст:\n{text}"
    )

    # Отправка админу и преподавателю
    kb = Keyboard(inline=True)
    kb.add(Text("↩️ Ответить", payload={"cmd": f"reply_{message.from_id}"}))
    try:
        await bot.api.messages.send(user_id=ADMIN_VK_ID, message=forward_msg, keyboard=kb.get_json(), random_id=0)
    except:
        pass
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if tutor and tutor.get("vk_id"):
        try:
            await bot.api.messages.send(user_id=tutor["vk_id"], message=forward_msg, keyboard=kb.get_json(), random_id=0)
        except:
            pass

    await message.answer("✅ Сообщение отправлено. Ожидайте ответа.",
                         keyboard=await get_main_menu(message.from_id))
    await state_dispenser.delete(message.from_id)


async def process_reply_button(event: MessageEvent):
    student_id = int(event.payload["cmd"].split("_")[1])
    await state_dispenser.update_data(event.user_id, reply_student_id=student_id)
    await bot.api.messages.send(user_id=event.user_id, message="Введите ваш ответ (текст):", random_id=0)
    await state_dispenser.set(event.user_id, ContactStates.waiting_reply)

@bot.on.private_message(state=ContactStates.waiting_reply)
async def send_reply_to_student(message: Message):
    data = await state_dispenser.get(message.from_id)
    student_id = data["reply_student_id"]
    reply_text = f"📬 Ответ от преподавателя:\n{message.text}"
    try:
        await bot.api.messages.send(user_id=student_id, message=reply_text, random_id=0)
        await message.answer("✅ Ответ отправлен ученику.", keyboard=await get_main_menu(message.from_id))
    except:
        await message.answer("⚠️ Не удалось отправить ответ (возможно, ученик заблокировал бота).",
                             keyboard=await get_main_menu(message.from_id))
    await state_dispenser.delete(message.from_id)

# ==================== СВЯЗЬ ПРЕПОДАВАТЕЛЯ С УЧЕНИКОМ ====================
@bot.on.private_message(text="✉️ Связь с учеником")
async def tutor_contact_student_start(message: Message):
    user_id = message.from_id
    tutor_id = await get_tutor_by_vk_id(user_id)
    if not tutor_id:
        await message.answer("Вы не зарегистрированы как преподаватель.")
        return

    bookings = await get_all_bookings()
    students = {}
    for b in bookings.values():
        if b["tutor_id"] == tutor_id and b["status"] in ("pending", "confirmed"):
            uid = b["user_id"]
            if uid not in students:
                students[uid] = b["username"]
    if not students:
        await message.answer("У вас пока нет учеников для связи.")
        return

    kb = Keyboard(inline=True)
    for uid, name in students.items():
        kb.add(Text(name, payload={"cmd": f"tutorcontactstudent_{uid}"}))
        kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await message.answer("Выберите ученика:", keyboard=kb.get_json())
    await state_dispenser.set(message.from_id, TutorContactStudentStates.choosing_student)


async def tutor_contact_student_chosen(event: MessageEvent):
    student_id = int(event.payload["cmd"].split("_")[-1])
    await state_dispenser.update_data(event.user_id, tutor_contact_student_id=student_id)
    student_username = "Неизвестный"
    bookings = await get_all_bookings()
    for b in bookings.values():
        if b["user_id"] == student_id:
            student_username = b["username"]
            break
    kb = Keyboard(inline=True)
    kb.add(Text("❌ Отмена", payload={"cmd": "cancel_tutor_msg_to_student"}))
    await event.edit_message(f"Вы пишете ученику {student_username}. Введите сообщение:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorContactStudentStates.waiting_message)


async def cancel_tutor_msg_to_student(event: MessageEvent):
    await state_dispenser.delete(event.user_id)
    tid = await get_tutor_by_vk_id(event.user_id)
    if tid:
        kb = Keyboard(inline=True)
        kb.add(Text("📋 Мои ученики", payload={"cmd": f"tutor_students_{tid}"}))
        kb.row()
        kb.add(Text("⚙️ Настроить расписание", payload={"cmd": f"tutor_schedule_{tid}"}))
        kb.row()
        kb.add(Text("📊 Статистика", payload={"cmd": f"tutor_stats_{tid}"}))
        kb.row()
        kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
        await event.edit_message("Панель преподавателя:", keyboard=kb.get_json())
    else:
        await event.edit_message("Главное меню:", keyboard=await get_main_menu(event.user_id))

@bot.on.private_message(state=TutorContactStudentStates.waiting_message)
async def tutor_send_message_to_student(message: Message):
    data = await state_dispenser.get(message.from_id)
    student_id = data["tutor_contact_student_id"]
    tutor_id = await get_tutor_by_vk_id(message.from_id)
    tutors = await get_all_tutors()
    tutor = tutors.get(tutor_id, {})
    tutor_name = tutor.get("name", "Преподаватель")

    forward_msg = f"📨 Сообщение от преподавателя {tutor_name}:\n\n{message.text}"
    try:
        await bot.api.messages.send(user_id=student_id, message=forward_msg, random_id=0)
        await message.answer("✅ Сообщение отправлено ученику.", keyboard=await get_main_menu(message.from_id))
    except:
        await message.answer("⚠️ Не удалось отправить сообщение (возможно, ученик заблокировал бота).",
                             keyboard=await get_main_menu(message.from_id))
    await state_dispenser.delete(message.from_id)

# ==================== ПОДДЕРЖКА ====================
@bot.on.private_message(text="🆘 Поддержка")
async def support_start(message: Message):
    kb = Keyboard(inline=True)
    kb.add(Text("❌ Отмена", payload={"cmd": "cancel_support"}))
    await message.answer("Опишите вашу проблему или вопрос. Администратор свяжется с вами.", keyboard=kb.get_json())
    await state_dispenser.set(message.from_id, SupportUserStates.waiting_message)


async def cancel_support(event: MessageEvent):
    await state_dispenser.delete(event.user_id)
    await event.edit_message("Обращение отменено.")
    await bot.api.messages.send(user_id=event.user_id, message="Главное меню", keyboard=await get_main_menu(event.user_id), random_id=0)

@bot.on.private_message(state=SupportUserStates.waiting_message)
async def support_message_to_admin(message: Message):
    user = await bot.api.users.get(message.from_id)
    username = f"{user[0].first_name} {user[0].last_name}" if user else str(message.from_id)
    uid = message.from_id
    text = message.text.strip()

    forward_msg = f"🆘 Сообщение в поддержку от {username} (ID: {uid}):\n\n{text}"
    kb = Keyboard(inline=True)
    kb.add(Text("↩️ Ответить", payload={"cmd": f"support_reply_{uid}"}))
    try:
        await bot.api.messages.send(user_id=ADMIN_VK_ID, message=forward_msg, keyboard=kb.get_json(), random_id=0)
    except:
        pass
    await message.answer("✅ Ваше сообщение отправлено администратору. Ожидайте ответа.",
                         keyboard=await get_main_menu(uid))
    await state_dispenser.delete(message.from_id)


async def support_reply_start(event: MessageEvent):
    if event.user_id != ADMIN_VK_ID:
        await event.answer("Только администратор может отвечать на обращения.")
        return
    student_id = int(event.payload["cmd"].split("_")[-1])
    await state_dispenser.update_data(event.user_id, support_reply_student_id=student_id)
    await bot.api.messages.send(user_id=event.user_id, message="Введите ответ пользователю:", random_id=0)
    await state_dispenser.set(event.user_id, SupportAdminReplyStates.waiting_reply)

@bot.on.private_message(state=SupportAdminReplyStates.waiting_reply)
async def support_send_reply(message: Message):
    data = await state_dispenser.get(message.from_id)
    student_id = data["support_reply_student_id"]
    reply_text = f"📬 Ответ от администратора:\n{message.text}"
    try:
        await bot.api.messages.send(user_id=student_id, message=reply_text, random_id=0)
        await message.answer("✅ Ответ отправлен пользователю.", keyboard=await get_main_menu(message.from_id))
    except:
        await message.answer("⚠️ Не удалось отправить ответ (возможно, пользователь заблокировал бота).",
                             keyboard=await get_main_menu(message.from_id))
    await state_dispenser.delete(message.from_id)

# ==================== АДМИН-ПАНЕЛЬ ====================
@bot.on.private_message(text="👨‍🏫 Админ-панель")
async def admin_panel(message: Message):
    if message.from_id != ADMIN_VK_ID:
        await message.answer("⛔ Доступ запрещён.")
        return
    kb = Keyboard(inline=True)
    kb.add(Text("➕ Добавить репетитора", payload={"cmd": "admin_add"}))
    kb.row()
    kb.add(Text("✏️ Редактировать репетитора", payload={"cmd": "admin_edit_list"}))
    kb.row()
    kb.add(Text("❌ Удалить репетитора", payload={"cmd": "admin_delete_list"}))
    kb.row()
    kb.add(Text("📊 Статистика", payload={"cmd": "admin_stats"}))
    kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await message.answer("Админ-панель управления репетиторами", keyboard=kb.get_json())


async def open_admin_panel(event: MessageEvent):
    await state_dispenser.delete(event.user_id)
    kb = Keyboard(inline=True)
    kb.add(Text("➕ Добавить репетитора", payload={"cmd": "admin_add"}))
    kb.row()
    kb.add(Text("✏️ Редактировать репетитора", payload={"cmd": "admin_edit_list"}))
    kb.row()
    kb.add(Text("❌ Удалить репетитора", payload={"cmd": "admin_delete_list"}))
    kb.row()
    kb.add(Text("📊 Статистика", payload={"cmd": "admin_stats"}))
    kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await event.edit_message("Админ-панель управления репетиторами", keyboard=kb.get_json())

# -------------------- Добавление репетитора --------------------

async def admin_add_start(event: MessageEvent):
    if event.user_id != ADMIN_VK_ID:
        return
    await state_dispenser.set(event.user_id, AdminStates.waiting_name)
    await event.edit_message("Введите имя репетитора:")
    # Ждём текстовое сообщение
    # Обработчик ловит любое сообщение в этом состоянии (см. ниже)

@bot.on.private_message(state=AdminStates.waiting_name)
async def admin_add_name(message: Message):
    await state_dispenser.update_data(message.from_id, name=message.text.strip())
    await message.answer("Введите описание репетитора (или '-' чтобы пропустить):")
    await state_dispenser.set(message.from_id, AdminStates.waiting_description)

@bot.on.private_message(state=AdminStates.waiting_description)
async def admin_add_description(message: Message):
    desc = message.text.strip()
    if desc == "-":
        desc = ""
    await state_dispenser.update_data(message.from_id, description=desc)
    await message.answer("Введите Telegram ID репетитора (число или 0, если нет):")
    await state_dispenser.set(message.from_id, AdminStates.waiting_telegram_id)

@bot.on.private_message(state=AdminStates.waiting_telegram_id)
async def admin_add_telegram_id(message: Message):
    try:
        tg_id = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число или 0.")
        return
    await state_dispenser.update_data(message.from_id, telegram_id=tg_id if tg_id != 0 else None)
    await message.answer("Введите VK ID репетитора (число или 0, если нет):")
    await state_dispenser.set(message.from_id, AdminStates.waiting_vk_id)


@bot.on.private_message(state=AdminStates.waiting_vk_id)
async def admin_add_vk_id(message: Message):
    try:
        vk_id = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число или 0.")
        return
    await state_dispenser.update_data(message.from_id, vk_id=vk_id if vk_id != 0 else None)
    await message.answer("Введите процент комиссии (целое число, по умолчанию 25):")
    await state_dispenser.set(message.from_id, AdminStates.waiting_commission)

@bot.on.private_message(state=AdminStates.waiting_commission)
async def admin_add_commission(message: Message):
    try:
        comm = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число.")
        return
    await state_dispenser.update_data(message.from_id, commission_percent=comm)
    await message.answer("Введите ИНН репетитора (или отправьте '-', чтобы пропустить):")
    await state_dispenser.set(message.from_id, AdminStates.waiting_inn)

@bot.on.private_message(state=AdminStates.waiting_inn)
async def admin_add_inn(message: Message):
    inn = message.text.strip()
    if inn == "-":
        inn = ""
    await state_dispenser.update_data(message.from_id, inn=inn)
    await state_dispenser.update_data(message.from_id, subjects={})
    await message.answer("Введите название первого предмета, который ведёт репетитор:")
    await state_dispenser.set(message.from_id, AdminStates.waiting_subject_name)

@bot.on.private_message(state=AdminStates.waiting_subject_name)
async def admin_add_subject_name(message: Message):
    subject = message.text.strip()
    await state_dispenser.update_data(message.from_id, temp_subject=subject)
    await message.answer(f"Введите цену за занятие для предмета «{subject}» (целое число рублей):")
    await state_dispenser.set(message.from_id, AdminStates.waiting_subject_price)

@bot.on.private_message(state=AdminStates.waiting_subject_price)
async def admin_add_subject_price(message: Message):
    try:
        price = int(message.text.strip())
    except ValueError:
        await message.answer("Пожалуйста, введите целое число.")
        return
    data = await state_dispenser.get(message.from_id)
    subjects = data.get("subjects", {})
    temp_subject = data.get("temp_subject")
    subjects[temp_subject] = price
    await state_dispenser.update_data(message.from_id, subjects=subjects)

    kb = Keyboard(inline=True)
    kb.add(Text("✅ Да, добавить ещё", payload={"cmd": "add_another_subject"}))
    kb.row()
    kb.add(Text("❌ Нет, закончить", payload={"cmd": "finish_adding_subjects"}))
    await message.answer(f"Предмет «{temp_subject}» с ценой {price} руб. добавлен. Добавить ещё предмет?",
                         keyboard=kb.get_json())


async def add_another_subject(event: MessageEvent):
    await event.edit_message("Введите название следующего предмета:")
    await state_dispenser.set(event.user_id, AdminStates.waiting_subject_name)


async def finish_adding_subjects(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    new_id = await add_tutor(
        name=data["name"],
        photo="",  # фото пока не поддерживается
        telegram_id=data.get("telegram_id"),
        description=data["description"],
        commission_percent=data.get("commission_percent", 25),
        inn=data.get("inn", ""),
        vk_id=data.get("vk_id")
    )
    subjects = data.get("subjects", {})
    for subj_name, subj_price in subjects.items():
        await add_subject(new_id, subj_name, subj_price)

    kb = Keyboard(inline=True)
    kb.add(Text("📂 В админ-панель", payload={"cmd": "admin_panel_open"}))
    await event.edit_message(f"✅ Репетитор «{data['name']}» успешно добавлен (ID {new_id}).", keyboard=kb.get_json())
    await state_dispenser.delete(event.user_id)

# -------------------- Редактирование репетитора --------------------

async def admin_edit_list(event: MessageEvent):
    tutors = await get_all_tutors()
    if not tutors:
        kb = Keyboard(inline=True)
        kb.add(Text("📂 В админ-панель", payload={"cmd": "admin_panel_open"}))
        await event.edit_message("Нет репетиторов для редактирования.", keyboard=kb.get_json())
        return
    kb = Keyboard(inline=True)
    for tid, tdata in tutors.items():
        kb.add(Text(tdata["name"], payload={"cmd": f"edit_tutor_{tid}"}))
        kb.row()
    kb.add(Text("📂 В админ-панель", payload={"cmd": "admin_panel_open"}))
    await event.edit_message("Выберите репетитора для редактирования:", keyboard=kb.get_json())


async def edit_tutor_choice(event: MessageEvent):
    tid = int(event.payload["cmd"].split("_")[-1])
    await state_dispenser.update_data(event.user_id, edit_tutor_id=tid)
    tutors = await get_all_tutors()
    tutor = tutors[tid]
    info = f"Редактирование: {tutor['name']}\n\nЧто хотите изменить?"
    kb = Keyboard(inline=True)
    kb.add(Text("Изменить имя", payload={"cmd": "edit_name"}))
    kb.row()
    kb.add(Text("Изменить описание", payload={"cmd": "edit_desc"}))
    kb.row()
    kb.add(Text("Изменить VK ID", payload={"cmd": "edit_vk_id"}))
    kb.row()
    kb.add(Text("📚 Управление предметами", payload={"cmd": "manage_subjects"}))
    kb.row()
    kb.add(Text("💰 Изменить комиссию", payload={"cmd": "edit_commission"}))
    kb.row()
    kb.add(Text("🔄 Режим комиссии", payload={"cmd": "toggle_commission_mode"}))
    kb.row()
    kb.add(Text("🔙 К списку", payload={"cmd": "admin_edit_list"}))
    await event.edit_message(info, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, AdminStates.waiting_edit_choice)


async def edit_field_choice(event: MessageEvent):
    field = event.payload["cmd"].split("_", 1)[1]
    await state_dispenser.update_data(event.user_id, edit_field=field)
    prompts = {
        "name": "Введите новое имя:",
        "desc": "Введите новое описание:",
        "vk_id": "Введите новый VK ID (число или 0, чтобы удалить):",
        "commission": "Введите новый процент комиссии (целое число):",
        "inn": "Введите новый ИНН (или '-', чтобы удалить):"
    }
    await event.edit_message(prompts.get(field, "Введите новое значение:"))
    await state_dispenser.set(event.user_id, AdminStates.waiting_new_value)

@bot.on.private_message(state=AdminStates.waiting_new_value)
async def process_new_value(message: Message):
    data = await state_dispenser.get(message.from_id)
    tid = data["edit_tutor_id"]
    field = data["edit_field"]

    kwargs = {}
    if field == "name":
        kwargs["name"] = message.text.strip()
    elif field == "desc":
        kwargs["description"] = message.text.strip()
    elif field == "telegram_id":
        try:
            new_id = int(message.text.strip())
            kwargs["telegram_id"] = new_id if new_id != 0 else None
        except ValueError:
            await message.answer("Введите целое число или 0.")
            return
    elif field == "commission":
        try:
            comm = int(message.text.strip())
            kwargs["commission_percent"] = comm
        except ValueError:
            await message.answer("Введите целое число.")
            return
    elif field == "inn":
        inn = message.text.strip()
        if inn == "-":
            inn = ""
        kwargs["inn"] = inn
    elif field == "vk_id":
        try:
            new_id = int(message.text.strip())
            kwargs["vk_id"] = new_id if new_id != 0 else None
        except ValueError:
            await message.answer("Введите целое число или 0.")
            return
    await update_tutor(tid, **kwargs)
    kb = Keyboard(inline=True)
    kb.add(Text("📂 В админ-панель", payload={"cmd": "admin_panel_open"}))
    await message.answer("✅ Изменения сохранены.", keyboard=kb.get_json())
    await state_dispenser.delete(message.from_id)


async def manage_subjects(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data.get("edit_tutor_id")
    tutors = await get_all_tutors()
    if not tid or tid not in tutors:
        await event.answer("Ошибка")
        return
    tutor = tutors[tid]
    text = f"Предметы репетитора «{tutor['name']}»:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    kb = Keyboard(inline=True)
    for subj in tutor["subjects"]:
        kb.add(Text(f"✏️ {subj}", payload={"cmd": f"editsubj_{subj}"}))
        kb.row()
    kb.add(Text("➕ Добавить предмет", payload={"cmd": "add_subject"}))
    kb.row()
    kb.add(Text("🔙 Назад к редактированию", payload={"cmd": "back_to_edit_tutor"}))
    await event.edit_message(text, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, AdminStates.managing_subjects)


async def back_to_edit_tutor(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data.get("edit_tutor_id")
    tutors = await get_all_tutors()
    if not tid or tid not in tutors:
        await event.answer("Ошибка")
        return
    tutor = tutors[tid]
    info = f"Редактирование: {tutor['name']}\n\nЧто хотите изменить?"
    kb = Keyboard(inline=True)
    kb.add(Text("Изменить имя", payload={"cmd": "edit_name"}))
    kb.row()
    kb.add(Text("Изменить описание", payload={"cmd": "edit_desc"}))
    kb.row()
    kb.add(Text("Изменить VK ID", payload={"cmd": "edit_vk_id"}))
    kb.row()
    kb.add(Text("📚 Управление предметами", payload={"cmd": "manage_subjects"}))
    kb.row()
    kb.add(Text("💰 Изменить комиссию", payload={"cmd": "edit_commission"}))
    kb.row()
    kb.add(Text("🔄 Режим комиссии", payload={"cmd": "toggle_commission_mode"}))
    kb.row()
    kb.add(Text("🔙 К списку", payload={"cmd": "admin_edit_list"}))
    await event.edit_message(info, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, AdminStates.waiting_edit_choice)


async def add_subject_start(event: MessageEvent):
    await event.edit_message("Введите название нового предмета:")
    await state_dispenser.set(event.user_id, AdminStates.adding_subject_name)

@bot.on.private_message(state=AdminStates.adding_subject_name)
async def process_adding_subject_name(message: Message):
    name = message.text.strip()
    data = await state_dispenser.get(message.from_id)
    tid = data.get("edit_tutor_id")
    tutors = await get_all_tutors()
    if tid and name in tutors[tid]["subjects"]:
        await message.answer("Такой предмет уже существует. Введите другое название.")
        return
    await state_dispenser.update_data(message.from_id, temp_new_subject=name)
    await message.answer(f"Введите цену за занятие для предмета «{name}» (целое число рублей):")
    await state_dispenser.set(message.from_id, AdminStates.adding_subject_price)

@bot.on.private_message(state=AdminStates.adding_subject_price)
async def process_adding_subject_price(message: Message):
    try:
        price = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число.")
        return
    data = await state_dispenser.get(message.from_id)
    tid = data.get("edit_tutor_id")
    name = data.get("temp_new_subject")
    await add_subject(tid, name, price)
    await message.answer(f"✅ Предмет «{name}» добавлен с ценой {price} руб.")
    # Показать снова список предметов
    tutors = await get_all_tutors()
    tutor = tutors[tid]
    text = f"Предметы репетитора «{tutor['name']}»:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    kb = Keyboard(inline=True)
    for subj in tutor["subjects"]:
        kb.add(Text(f"✏️ {subj}", payload={"cmd": f"editsubj_{subj}"}))
        kb.row()
    kb.add(Text("➕ Добавить предмет", payload={"cmd": "add_subject"}))
    kb.row()
    kb.add(Text("🔙 Назад к редактированию", payload={"cmd": "back_to_edit_tutor"}))
    await message.answer(text, keyboard=kb.get_json())
    await state_dispenser.set(message.from_id, AdminStates.managing_subjects)


async def edit_subject_menu(event: MessageEvent):
    subj_name = event.payload["cmd"].split("_", 1)[1]
    await state_dispenser.update_data(event.user_id, edit_subject_name=subj_name)
    kb = Keyboard(inline=True)
    kb.add(Text("✏️ Изменить название", payload={"cmd": "editsubj_name"}))
    kb.row()
    kb.add(Text("💰 Изменить цену", payload={"cmd": "editsubj_price"}))
    kb.row()
    kb.add(Text("❌ Удалить предмет", payload={"cmd": "editsubj_delete"}))
    kb.row()
    kb.add(Text("🔙 Назад к списку предметов", payload={"cmd": "back_to_subjects_list"}))
    await event.edit_message(f"Предмет: {subj_name}\nВыберите действие:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, AdminStates.editing_subject_choice)


async def back_to_subjects_list(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data.get("edit_tutor_id")
    tutors = await get_all_tutors()
    tutor = tutors[tid]
    text = f"Предметы репетитора «{tutor['name']}»:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    kb = Keyboard(inline=True)
    for subj in tutor["subjects"]:
        kb.add(Text(f"✏️ {subj}", payload={"cmd": f"editsubj_{subj}"}))
        kb.row()
    kb.add(Text("➕ Добавить предмет", payload={"cmd": "add_subject"}))
    kb.row()
    kb.add(Text("🔙 Назад к редактированию", payload={"cmd": "back_to_edit_tutor"}))
    await event.edit_message(text, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, AdminStates.managing_subjects)


async def edit_subject_name_start(event: MessageEvent):
    await event.edit_message("Введите новое название предмета:")
    await state_dispenser.set(event.user_id, AdminStates.editing_subject_name_state)

@bot.on.private_message(state=AdminStates.editing_subject_name_state)
async def process_new_subject_name(message: Message):
    new_name = message.text.strip()
    data = await state_dispenser.get(message.from_id)
    tid = data.get("edit_tutor_id")
    old_name = data.get("edit_subject_name")
    await update_subject(tid, old_name, new_name=new_name)
    await message.answer(f"✅ Название предмета изменено на «{new_name}».")
    # Вернуться к списку предметов
    tutors = await get_all_tutors()
    tutor = tutors[tid]
    text = f"Предметы репетитора «{tutor['name']}»:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    kb = Keyboard(inline=True)
    for subj in tutor["subjects"]:
        kb.add(Text(f"✏️ {subj}", payload={"cmd": f"editsubj_{subj}"}))
        kb.row()
    kb.add(Text("➕ Добавить предмет", payload={"cmd": "add_subject"}))
    kb.row()
    kb.add(Text("🔙 Назад к редактированию", payload={"cmd": "back_to_edit_tutor"}))
    await message.answer(text, keyboard=kb.get_json())
    await state_dispenser.set(message.from_id, AdminStates.managing_subjects)
    await state_dispenser.update_data(message.from_id, edit_subject_name=None)


async def edit_subject_price_start(event: MessageEvent):
    await event.edit_message("Введите новую цену (целое число):")
    await state_dispenser.set(event.user_id, AdminStates.editing_subject_price_state)

@bot.on.private_message(state=AdminStates.editing_subject_price_state)
async def process_new_subject_price(message: Message):
    try:
        new_price = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число.")
        return
    data = await state_dispenser.get(message.from_id)
    tid = data.get("edit_tutor_id")
    subj = data.get("edit_subject_name")
    await update_subject(tid, subj, new_price=new_price)
    await message.answer(f"✅ Цена для предмета «{subj}» изменена на {new_price} руб.")
    # Вернуться к списку предметов
    tutors = await get_all_tutors()
    tutor = tutors[tid]
    text = f"Предметы репетитора «{tutor['name']}»:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    kb = Keyboard(inline=True)
    for subj in tutor["subjects"]:
        kb.add(Text(f"✏️ {subj}", payload={"cmd": f"editsubj_{subj}"}))
        kb.row()
    kb.add(Text("➕ Добавить предмет", payload={"cmd": "add_subject"}))
    kb.row()
    kb.add(Text("🔙 Назад к редактированию", payload={"cmd": "back_to_edit_tutor"}))
    await message.answer(text, keyboard=kb.get_json())
    await state_dispenser.set(message.from_id, AdminStates.managing_subjects)


async def delete_subject_confirm(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    subj = data.get("edit_subject_name")
    kb = Keyboard(inline=True)
    kb.add(Text("✅ Да, удалить", payload={"cmd": "confirm_delete_subject"}))
    kb.row()
    kb.add(Text("❌ Отмена", payload={"cmd": "back_to_subjects_list"}))
    await event.edit_message(f"Удалить предмет «{subj}»?", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, AdminStates.deleting_subject_confirm)


async def confirm_delete_subject(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data.get("edit_tutor_id")
    subj = data.get("edit_subject_name")
    await delete_subject(tid, subj)
    await event.edit_message(f"✅ Предмет «{subj}» удалён.")
    # Вернуться к списку предметов
    tutors = await get_all_tutors()
    tutor = tutors[tid]
    text = f"Предметы репетитора «{tutor['name']}»:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    kb = Keyboard(inline=True)
    for subj in tutor["subjects"]:
        kb.add(Text(f"✏️ {subj}", payload={"cmd": f"editsubj_{subj}"}))
        kb.row()
    kb.add(Text("➕ Добавить предмет", payload={"cmd": "add_subject"}))
    kb.row()
    kb.add(Text("🔙 Назад к редактированию", payload={"cmd": "back_to_edit_tutor"}))
    await event.edit_message(text, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, AdminStates.managing_subjects)


async def toggle_commission_mode(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data.get("edit_tutor_id")
    if not tid:
        return
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    current_mode = tutor.get("commission_mode", "manual")
    new_mode = "auto" if current_mode == "manual" else "manual"
    await update_tutor(tid, commission_mode=new_mode)
    await event.edit_message(
        f"Режим комиссии изменён на {'автоматический' if new_mode=='auto' else 'ручной'}.\n"
        "При автоматическом режиме процент рассчитывается по прогрессивной шкале."
    )
    # Вернуться в меню редактирования
    await edit_tutor_choice(event)  # повторно вызвать (передать event)

# -------------------- Удаление репетитора --------------------

async def admin_delete_list(event: MessageEvent):
    tutors = await get_all_tutors()
    if not tutors:
        kb = Keyboard(inline=True)
        kb.add(Text("📂 В админ-панель", payload={"cmd": "admin_panel_open"}))
        await event.edit_message("Нет репетиторов для удаления.", keyboard=kb.get_json())
        return
    kb = Keyboard(inline=True)
    for tid, tdata in tutors.items():
        kb.add(Text(tdata["name"], payload={"cmd": f"del_tutor_{tid}"}))
        kb.row()
    kb.add(Text("📂 В админ-панель", payload={"cmd": "admin_panel_open"}))
    await event.edit_message("Выберите репетитора для удаления:", keyboard=kb.get_json())


async def delete_tutor_confirm(event: MessageEvent):
    tid = int(event.payload["cmd"].split("_")[-1])
    tutors = await get_all_tutors()
    tutor = tutors[tid]
    await state_dispenser.update_data(event.user_id, del_tutor_id=tid)
    kb = Keyboard(inline=True)
    kb.add(Text("✅ Да, удалить", payload={"cmd": "confirm_delete"}))
    kb.row()
    kb.add(Text("❌ Отмена", payload={"cmd": "admin_delete_list"}))
    await event.edit_message(f"Удалить репетитора «{tutor['name']}»?", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, AdminStates.waiting_delete_confirm)


async def confirm_delete(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data["del_tutor_id"]
    tutors = await get_all_tutors()
    name = tutors[tid]["name"]
    await delete_tutor(tid)
    kb = Keyboard(inline=True)
    kb.add(Text("📂 В админ-панель", payload={"cmd": "admin_panel_open"}))
    await event.edit_message(f"✅ Репетитор «{name}» удалён.", keyboard=kb.get_json())
    await state_dispenser.delete(event.user_id)

# -------------------- Админ-статистика --------------------

async def admin_stats_menu(event: MessageEvent):
    kb = Keyboard(inline=True)
    kb.add(Text("👨‍🏫 Статистика по репетиторам", payload={"cmd": "admin_stats_tutors"}))
    kb.row()
    kb.add(Text("👤 Статистика по ученикам", payload={"cmd": "admin_stats_students"}))
    kb.row()
    kb.add(Text("🔙 В админ-панель", payload={"cmd": "admin_panel_open"}))
    await event.edit_message("📊 Административная статистика\nВыберите раздел:", keyboard=kb.get_json())


async def admin_stats_tutors_overview(event: MessageEvent):
    stats = await get_all_tutors_stats()
    lines = ["📊 Статистика по репетиторам (за всё время):\n"]
    total_lessons = total_income = total_commission = 0.0
    for t in stats:
        lines.append(f"👨‍🏫 {t['name']}:")
        lines.append(f"   Занятий: {t['total_lessons']}")
        lines.append(f"   Доход: {t['total_income']:.2f} руб.")
        lines.append(f"   Комиссия: {t['commission']:.2f} руб.")
        lines.append(f"   Доход после комиссии: {t['net_income']:.2f} руб.")
        lines.append("")
        total_lessons += t['total_lessons']
        total_income += t['total_income']
        total_commission += t['commission']
    lines.append(f"📌 Общий итог:")
    lines.append(f"   Всего занятий: {total_lessons}")
    lines.append(f"   Общий доход: {total_income:.2f} руб.")
    lines.append(f"   Общая комиссия: {total_commission:.2f} руб.")
    text = "\n".join(lines)
    now = datetime.now()
    months = sorted(set((d.year, d.month) for d in [now - timedelta(days=30 * i) for i in range(12)]), reverse=True)
    kb = Keyboard(inline=True)
    for y, m in months:
        kb.add(Text(f"{y}-{m:02d}", payload={"cmd": f"admin_stats_tutors_month_{y}_{m}"}))
        kb.row()
    kb.add(Text("🔙 К разделам статистики", payload={"cmd": "admin_stats"}))
    await event.edit_message(text, keyboard=kb.get_json())


async def admin_stats_tutors_month(event: MessageEvent):
    parts = event.payload["cmd"].split("_")
    year = int(parts[4])
    month = int(parts[5])
    stats = await get_all_tutors_stats_by_month(year, month)
    lines = [f"📊 Статистика по репетиторам за {year}-{month:02d}:\n"]
    total_lessons = total_income = total_commission = 0.0
    for t in stats:
        lines.append(f"👨‍🏫 {t['name']}:")
        lines.append(f"   Занятий: {t['total_lessons']}")
        lines.append(f"   Доход: {t['total_income']:.2f} руб.")
        lines.append(f"   Комиссия: {t['commission']:.2f} руб.")
        lines.append(f"   Доход после комиссии: {t['net_income']:.2f} руб.")
        lines.append("")
        total_lessons += t['total_lessons']
        total_income += t['total_income']
        total_commission += t['commission']
    lines.append(f"📌 Общий итог:")
    lines.append(f"   Всего занятий: {total_lessons}")
    lines.append(f"   Общий доход: {total_income:.2f} руб.")
    lines.append(f"   Общая комиссия: {total_commission:.2f} руб.")
    text = "\n".join(lines)
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 К общей статистике", payload={"cmd": "admin_stats_tutors"}))
    await event.edit_message(text, keyboard=kb.get_json())


async def admin_stats_students(event: MessageEvent):
    stats = await get_students_stats()
    if not stats:
        text = "Нет данных."
    else:
        lines = ["📊 Статистика по ученикам:\n"]
        for s in stats:
            lines.append(f"👤 {s['username']} (ID: {s['user_id']})")
            lines.append(f"   Проведено занятий: {s['completed_lessons']}")
            lines.append(f"   Оставшихся по абонементам: {s['remaining_subscription_lessons']}")
            lines.append("")
        text = "\n".join(lines)
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 К разделам статистики", payload={"cmd": "admin_stats"}))
    await event.edit_message(text, keyboard=kb.get_json())

# ==================== ПАНЕЛЬ ПРЕПОДАВАТЕЛЯ ====================
@bot.on.private_message(text="👨‍🏫 Панель преподавателя")
async def tutor_panel(message: Message):
    user_id = message.from_id
    tutor_id = await get_tutor_by_vk_id(user_id)
    if not tutor_id:
        await message.answer("⛔ Вы не зарегистрированы как преподаватель.")
        return
    kb = Keyboard(inline=True)
    kb.add(Text("👤 Моя анкета", payload={"cmd": f"tutor_profile_{tutor_id}"}))
    kb.row()
    kb.add(Text("📋 Мои ученики", payload={"cmd": f"tutor_students_{tutor_id}"}))
    kb.row()
    kb.add(Text("⚙️ Настроить расписание", payload={"cmd": f"tutor_schedule_{tutor_id}"}))
    kb.row()
    kb.add(Text("📊 Статистика", payload={"cmd": f"tutor_stats_{tutor_id}"}))
    kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await message.answer("Панель преподавателя:", keyboard=kb.get_json())


async def back_to_tutor_panel(event: MessageEvent):
    tid = int(event.payload["cmd"].split("_")[-1])
    kb = Keyboard(inline=True)
    kb.add(Text("👤 Моя анкета", payload={"cmd": f"tutor_profile_{tid}"}))
    kb.row()
    kb.add(Text("📋 Мои ученики", payload={"cmd": f"tutor_students_{tid}"}))
    kb.row()
    kb.add(Text("⚙️ Настроить расписание", payload={"cmd": f"tutor_schedule_{tid}"}))
    kb.row()
    kb.add(Text("📊 Статистика", payload={"cmd": f"tutor_stats_{tid}"}))
    kb.row()
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await event.edit_message("Панель преподавателя:", keyboard=kb.get_json())


async def show_tutor_own_profile(event: MessageEvent):
    tid = int(event.payload["cmd"].split("_")[-1])
    user_tutor_id = await get_tutor_by_vk_id(event.user_id)
    if user_tutor_id != tid:
        await event.answer("Доступ запрещён.")
        return
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        await event.edit_message("Анкета не найдена.")
        return
    text = tutor["description"] + "\n\nПредметы и цены:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 Назад в панель", payload={"cmd": f"back_to_tutor_panel_{tid}"}))
    await event.edit_message(text, keyboard=kb.get_json())

# Мои ученики (для преподавателя)

async def show_students(event: MessageEvent):
    tid = int(event.payload["cmd"].split("_")[-1])
    bookings = await get_all_bookings()
    students = {}
    for bid, b in bookings.items():
        if b["tutor_id"] == tid and b["status"] in ("pending", "confirmed"):
            uid = b["user_id"]
            students.setdefault(uid, {"username": b["username"], "bookings": []})
            students[uid]["bookings"].append((bid, b))
    if not students:
        kb = Keyboard(inline=True)
        kb.add(Text("🔙 Назад", payload={"cmd": f"back_to_tutor_panel_{tid}"}))
        await event.edit_message("У вас пока нет активных записей.", keyboard=kb.get_json())
        return

    text = "📋 Ваши ученики:\n\n"
    kb = Keyboard(inline=True)
    for uid, sdata in students.items():
        lessons_count = sum(1 for _, b in sdata["bookings"] if b["status"] in ("confirmed","completed"))
        text += f"👤 {sdata['username']} (занятий: {lessons_count})\n"
        for bid, b in sdata["bookings"]:
            status_emoji = "⏳" if b["status"] == "pending" else "✅"
            text += f"  {status_emoji} {b['date']} {b['time_slot']} – {b['subject']}\n"
            if b["status"] == "pending":
                kb.add(Text(f"✅ Подтвердить {b['username']} {b['date']} {b['time_slot']}",
                            payload={"cmd": f"tutor_confirm_{bid}"}))
                kb.add(Text(f"❌ Отклонить", payload={"cmd": f"tutor_reject_{bid}"}))
                kb.row()
            elif b["status"] == "confirmed":
                dt = datetime.strptime(b["date"] + " " + b["time_slot"].split("-")[0], "%d.%m.%Y %H:%M")
                if (dt - datetime.now()) > timedelta(hours=24):
                    kb.add(Text(f"❌ Отменить", payload={"cmd": f"tutor_cancel_{bid}"}))
                    kb.add(Text(f"🔄 Перенести", payload={"cmd": f"tutor_reschedule_{bid}"}))
                    kb.row()
        text += "\n"
    kb.add(Text("🔙 Назад", payload={"cmd": f"back_to_tutor_panel_{tid}"}))
    await event.edit_message(text, keyboard=kb.get_json())

# Подтверждение/отклонение заявок преподавателем

async def tutor_confirm_booking(event: MessageEvent):
    bid = int(event.payload["cmd"].split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking or booking["status"] != "pending":
        await event.edit_message("Заявка уже обработана.")
        return

    user_id = booking["user_id"]
    email = await get_user_email(user_id)
    if email:
        await create_and_send_payment(event, booking, email, bid)
        await event.edit_message("Заявка подтверждена. Отправлена ссылка на оплату.")
    else:
        await set_pending_email_request(user_id, bid)
        await event.edit_message("Заявка подтверждена. Запрашиваем email ученика для чека...")
        await bot.api.messages.send(user_id=user_id,
                                    message="📧 Для завершения записи и получения чека введите ваш адрес электронной почты:",
                                    random_id=0)


async def tutor_reject_booking(event: MessageEvent):
    bid = int(event.payload["cmd"].split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking or booking["status"] != "pending":
        await event.edit_message("Заявка уже обработана.")
        return
    user_id = booking["user_id"]
    await delete_booking(bid)
    await bot.api.messages.send(user_id=user_id,
                                message="❌ Ваша заявка на занятие была отклонена преподавателем. Вы можете записаться на другое время.",
                                random_id=0)
    tid = booking["tutor_id"]
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 К списку учеников", payload={"cmd": f"tutor_students_{tid}"}))
    await event.edit_message("❌ Заявка отклонена.", keyboard=kb.get_json())

# Отмена и перенос преподавателем

async def tutor_cancel_booking(event: MessageEvent):
    bid = int(event.payload["cmd"].split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking or booking["status"] != "confirmed":
        await event.edit_message("Невозможно отменить.")
        return
    dt = datetime.strptime(booking["date"] + " " + booking["time_slot"].split("-")[0], "%d.%m.%Y %H:%M")
    if (dt - datetime.now()) <= timedelta(hours=24):
        await event.edit_message("Отмена менее чем за 24 часа невозможна.")
        return
    await update_booking(bid, status="cancelled")
    student_id = booking["user_id"]
    tutors = await get_all_tutors()
    tutor_name = tutors.get(booking["tutor_id"], {}).get("name", "Преподаватель")
    msg = f"❌ Преподаватель {tutor_name} отменил занятие {booking['date']} {booking['time_slot']} по предмету «{booking['subject']}»."
    await bot.api.messages.send(user_id=student_id, message=msg, random_id=0)
    tid = booking["tutor_id"]
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 К списку учеников", payload={"cmd": f"tutor_students_{tid}"}))
    await event.edit_message("✅ Занятие отменено.", keyboard=kb.get_json())


async def tutor_reschedule_start(event: MessageEvent):
    bid = int(event.payload["cmd"].split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking or booking["status"] != "confirmed":
        await event.edit_message("Невозможно перенести.")
        return
    dt = datetime.strptime(booking["date"] + " " + booking["time_slot"].split("-")[0], "%d.%m.%Y %H:%M")
    if (dt - datetime.now()) <= timedelta(hours=24):
        await event.edit_message("Перенос менее чем за 24 часа невозможен.")
        return
    await state_dispenser.set(event.user_id, TutorRescheduleStates.waiting_date)
    await state_dispenser.update_data(event.user_id,
        old_booking_id=bid,
        tutor_id=booking["tutor_id"],
        subject=booking["subject"],
        student_id=booking["user_id"],
        student_username=booking["username"],
        old_date=booking["date"],
        old_time=booking["time_slot"]
    )
    dates = await get_available_dates(booking["tutor_id"])
    if not dates:
        await event.edit_message("Нет доступных дат для переноса.")
        return
    kb = Keyboard(inline=True)
    for d in dates:
        dt_date = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt_date.weekday()]]})"
        kb.add(Text(label, payload={"cmd": f"t_reschedule_date_{d}"}))
        kb.row()
    kb.add(Text("🔙 Отмена", payload={"cmd": "back_to_menu"}))
    await event.edit_message("Выберите новую дату:", keyboard=kb.get_json())


async def tutor_reschedule_date(event: MessageEvent):
    date_str = event.payload["cmd"].split("t_reschedule_date_")[1]
    await state_dispenser.update_data(event.user_id, new_date=date_str)
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    old_bid = data["old_booking_id"]
    slots = await get_available_slots(tid, date_str, exclude_booking_id=old_bid)
    if not slots:
        kb = Keyboard(inline=True)
        kb.add(Text("🔙 К выбору даты", payload={"cmd": "back_tutor_reschedule_date"}))
        await event.edit_message("На эту дату нет свободных слотов.", keyboard=kb.get_json())
        return
    kb = Keyboard(inline=True)
    for s in slots:
        kb.add(Text(s, payload={"cmd": f"t_reschedule_slot_{s}"}))
        kb.row()
    kb.add(Text("🔙 К выбору даты", payload={"cmd": "back_tutor_reschedule_date"}))
    await event.edit_message("Выберите новое время:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorRescheduleStates.waiting_time)


async def back_tutor_reschedule_date(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data["tutor_id"]
    dates = await get_available_dates(tid)
    kb = Keyboard(inline=True)
    for d in dates:
        dt_date = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt_date.weekday()]]})"
        kb.add(Text(label, payload={"cmd": f"t_reschedule_date_{d}"}))
        kb.row()
    kb.add(Text("🔙 Отмена", payload={"cmd": "back_to_menu"}))
    await event.edit_message("Выберите новую дату:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorRescheduleStates.waiting_date)


async def tutor_reschedule_slot(event: MessageEvent):
    slot = event.payload["cmd"].split("t_reschedule_slot_")[1]
    await state_dispenser.update_data(event.user_id, new_time=slot)
    data = await state_dispenser.get(event.user_id)
    text = (
        f"Перенос занятия:\n"
        f"Ученик: {data['student_username']}\n"
        f"Предмет: {data['subject']}\n"
        f"Старое: {data['old_date']} {data['old_time']}\n"
        f"Новое: {data['new_date']} {slot}\n\nПодтвердить перенос?"
    )
    kb = Keyboard(inline=True)
    kb.add(Text("✅ Подтвердить", payload={"cmd": "confirm_tutor_reschedule"}))
    kb.row()
    kb.add(Text("🔙 Назад", payload={"cmd": "back_tutor_reschedule_date"}))
    await event.edit_message(text, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorRescheduleStates.waiting_confirmation)


async def confirm_tutor_reschedule(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    old_bid = data["old_booking_id"]
    tid = data["tutor_id"]
    new_date = data["new_date"]
    new_time = data["new_time"]
    subject = data["subject"]
    student_id = data["student_id"]
    student_username = data["student_username"]

    await update_booking(old_bid, status="cancelled")
    new_id = await add_booking(tid, student_id, student_username, subject, new_date, new_time)
    await update_booking(new_id, status="confirmed", reminded=0)

    student_msg = (
        f"🔄 Преподаватель перенёс занятие.\n"
        f"Предмет: {subject}\n"
        f"Новое время: {new_date} {new_time}"
    )
    await bot.api.messages.send(user_id=student_id, message=student_msg, random_id=0)

    tutor_msg = f"✅ Вы перенесли занятие с {student_username} на {new_date} {new_time}."
    await bot.api.messages.send(user_id=event.user_id, message=tutor_msg, random_id=0)

    kb = Keyboard(inline=True)
    kb.add(Text("🔙 К списку учеников", payload={"cmd": f"tutor_students_{tid}"}))
    await event.edit_message("Перенос выполнен.", keyboard=kb.get_json())
    await state_dispenser.delete(event.user_id)

# -------------------- Настройка расписания преподавателем --------------------

async def schedule_main(event: MessageEvent):
    tid = int(event.payload["cmd"].split("_")[-1])
    await state_dispenser.update_data(event.user_id, tid=tid)
    sched = await get_schedule(tid)
    text = "Ваше расписание:\n"
    for day in WEEKDAYS:
        slots = sched.get(day, [])
        blocked = await is_day_blocked(tid, day)
        if blocked:
            icon = "🔒"
            info = "заблокирован"
        else:
            icon = "✅" if slots else ""
            info = ', '.join(slots) if slots else 'нет'
        text += f"{icon} {WEEKDAY_NAMES[day]}: {info}\n"
    kb = Keyboard(inline=True)
    for day in WEEKDAYS:
        kb.add(Text(f"✏️ {WEEKDAY_NAMES[day]}", payload={"cmd": f"sched_day_{day}"}))
        kb.row()
    kb.add(Text("🔙 Назад", payload={"cmd": f"back_to_tutor_panel_{tid}"}))
    await event.edit_message(text, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorScheduleStates.choose_day)


async def edit_day(event: MessageEvent):
    day = event.payload["cmd"].split("_")[2]
    await state_dispenser.update_data(event.user_id, current_day=day)
    data = await state_dispenser.get(event.user_id)
    tid = data["tid"]
    sched = await get_schedule(tid)
    slots = sched.get(day, [])
    blocked = await is_day_blocked(tid, day)

    status_line = "🔒 День заблокирован (запись недоступна)\n" if blocked else ""
    text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + status_line
    text += "\n".join(f"• {s}" for s in slots) if slots else "Нет слотов."

    kb = Keyboard(inline=True)
    kb.add(Text("➕ Добавить слот", payload={"cmd": "add_slot"}))
    kb.row()
    kb.add(Text("📅 Заполнить промежуток", payload={"cmd": "add_range"}))
    kb.row()
    if slots:
        kb.add(Text("❌ Удалить слот", payload={"cmd": "del_slot"}))
        kb.row()
    if blocked:
        kb.add(Text("🔓 Разблокировать день", payload={"cmd": f"unblock_day_{day}"}))
    else:
        kb.add(Text("🔒 Заблокировать день", payload={"cmd": f"block_day_{day}"}))
    kb.row()
    kb.add(Text("🔙 К дням недели", payload={"cmd": "back_to_schedule"}))
    await event.edit_message(text, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorScheduleStates.manage_day_slots)


async def back_to_schedule(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data["tid"]
    sched = await get_schedule(tid)
    text = "Ваше расписание:\n"
    for day in WEEKDAYS:
        slots = sched.get(day, [])
        blocked = await is_day_blocked(tid, day)
        icon = "🔒" if blocked else ("✅" if slots else "")
        text += f"{icon} {WEEKDAY_NAMES[day]}: {', '.join(slots) if slots else 'нет'}\n"
    kb = Keyboard(inline=True)
    for day in WEEKDAYS:
        kb.add(Text(f"✏️ {WEEKDAY_NAMES[day]}", payload={"cmd": f"sched_day_{day}"}))
        kb.row()
    kb.add(Text("🔙 Назад", payload={"cmd": f"back_to_tutor_panel_{tid}"}))
    await event.edit_message(text, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorScheduleStates.choose_day)


async def handle_block_day(event: MessageEvent):
    day = event.payload["cmd"].split("block_day_")[1]
    data = await state_dispenser.get(event.user_id)
    tid = data["tid"]
    await block_day(tid, day)
    await edit_day(event)  # перерисовать


async def handle_unblock_day(event: MessageEvent):
    day = event.payload["cmd"].split("unblock_day_")[1]
    data = await state_dispenser.get(event.user_id)
    tid = data["tid"]
    await unblock_day(tid, day)
    await edit_day(event)


async def add_slot_start(event: MessageEvent):
    await event.edit_message("Введите временной слот в формате HH:MM-HH:MM, например 10:00-11:30:")
    await state_dispenser.set(event.user_id, TutorScheduleStates.add_slot)

@bot.on.private_message(state=TutorScheduleStates.add_slot)
async def process_add_slot(message: Message):
    raw_slot = message.text.strip()
    if "-" not in raw_slot:
        await message.answer("Неверный формат. Используйте ЧЧ:ММ-ЧЧ:ММ")
        return
    parts = raw_slot.split("-")
    if len(parts) != 2:
        await message.answer("Неверный формат.")
        return
    start = clean_time_input(parts[0])
    end = clean_time_input(parts[1])
    for t in (start, end):
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError:
            await message.answer(f"Некорректное время «{t}». Пожалуйста, введите слот в формате ЧЧ:ММ-ЧЧ:ММ.")
            return
    slot = f"{start}-{end}"
    data = await state_dispenser.get(message.from_id)
    tid = data["tid"]
    day = data["current_day"]
    await add_schedule_slot(tid, day, slot)

    # Обновить отображение
    sched = await get_schedule(tid)
    slots = sched.get(day, [])
    text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + "\n".join(f"• {s}" for s in slots)
    kb = Keyboard(inline=True)
    kb.add(Text("➕ Добавить слот", payload={"cmd": "add_slot"}))
    kb.row()
    kb.add(Text("📅 Заполнить промежуток", payload={"cmd": "add_range"}))
    kb.row()
    kb.add(Text("❌ Удалить слот", payload={"cmd": "del_slot"}))
    kb.row()
    kb.add(Text("🔙 К дням недели", payload={"cmd": "back_to_schedule"}))
    await message.answer(text, keyboard=kb.get_json())
    await state_dispenser.set(message.from_id, TutorScheduleStates.manage_day_slots)


async def add_range_start(event: MessageEvent):
    kb = Keyboard(inline=True)
    kb.add(Text("60 минут", payload={"cmd": "dur_60"}))
    kb.row()
    kb.add(Text("90 минут", payload={"cmd": "dur_90"}))
    kb.row()
    kb.add(Text("🔙 Отмена", payload={"cmd": "back_to_schedule"}))
    await event.edit_message("Выберите длительность занятия:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorScheduleStates.range_duration)


async def range_duration_chosen(event: MessageEvent):
    duration = int(event.payload["cmd"].split("_")[1])
    await state_dispenser.update_data(event.user_id, range_duration=duration)
    kb = Keyboard(inline=True)
    kb.add(Text("Без перерыва", payload={"cmd": "brk_0"}))
    kb.row()
    kb.add(Text("10 минут", payload={"cmd": "brk_10"}))
    kb.row()
    kb.add(Text("15 минут", payload={"cmd": "brk_15"}))
    kb.row()
    kb.add(Text("20 минут", payload={"cmd": "brk_20"}))
    kb.row()
    kb.add(Text("🔙 Назад", payload={"cmd": "add_range_back"}))
    await event.edit_message("Нужен ли перерыв между занятиями?", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorScheduleStates.range_break)


async def range_break_back(event: MessageEvent):
    kb = Keyboard(inline=True)
    kb.add(Text("60 минут", payload={"cmd": "dur_60"}))
    kb.row()
    kb.add(Text("90 минут", payload={"cmd": "dur_90"}))
    kb.row()
    kb.add(Text("🔙 Отмена", payload={"cmd": "back_to_schedule"}))
    await event.edit_message("Выберите длительность занятия:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorScheduleStates.range_duration)


async def range_break_chosen(event: MessageEvent):
    break_min = int(event.payload["cmd"].split("_")[1])
    await state_dispenser.update_data(event.user_id, range_break=break_min)
    await event.edit_message(
        "Введите промежуток времени в формате ЧЧ:ММ-ЧЧ:ММ (например, 09:00-15:30).\n"
        "Бот автоматически разобьёт его на слоты с учётом выбранной длительности и перерыва."
    )
    await state_dispenser.set(event.user_id, TutorScheduleStates.add_range)

@bot.on.private_message(state=TutorScheduleStates.add_range)
async def process_add_range(message: Message):
    text = message.text.strip()
    if "-" not in text:
        await message.answer("Неверный формат. Используйте ЧЧ:ММ-ЧЧ:ММ")
        return
    parts = text.split("-")
    if len(parts) != 2:
        await message.answer("Неверный формат.")
        return
    start_time = clean_time_input(parts[0])
    end_time = clean_time_input(parts[1])
    for t in (start_time, end_time):
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError:
            await message.answer(f"Некорректное время «{t}». Пожалуйста, используйте формат ЧЧ:ММ.")
            return

    data = await state_dispenser.get(message.from_id)
    tid = data["tid"]
    day = data["current_day"]
    duration_min = data.get("range_duration", 90)
    break_min = data.get("range_break", 0)

    slots = split_into_slots(start_time, end_time, duration_min=duration_min, break_min=break_min)
    if not slots:
        await message.answer("Не удалось создать ни одного слота. Проверьте время.")
        return

    sched = await get_schedule(tid)
    existing = sched.get(day, [])
    added = 0
    for s in slots:
        if s not in existing:
            await add_schedule_slot(tid, day, s)
            added += 1

    await message.answer(f"Добавлено {added} новых слотов.")
    # Обновить отображение
    sched = await get_schedule(tid)
    slots = sched.get(day, [])
    text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + "\n".join(f"• {s}" for s in slots)
    kb = Keyboard(inline=True)
    kb.add(Text("➕ Добавить слот", payload={"cmd": "add_slot"}))
    kb.row()
    kb.add(Text("📅 Заполнить промежуток", payload={"cmd": "add_range"}))
    kb.row()
    kb.add(Text("❌ Удалить слот", payload={"cmd": "del_slot"}))
    kb.row()
    kb.add(Text("🔙 К дням недели", payload={"cmd": "back_to_schedule"}))
    await message.answer(text, keyboard=kb.get_json())
    await state_dispenser.set(message.from_id, TutorScheduleStates.manage_day_slots)


async def del_slot_start(event: MessageEvent):
    data = await state_dispenser.get(event.user_id)
    tid = data["tid"]
    day = data["current_day"]
    sched = await get_schedule(tid)
    slots = sched.get(day, [])
    if not slots:
        await event.edit_message("Нет слотов для удаления.")
        return
    kb = Keyboard(inline=True)
    for s in slots:
        kb.add(Text(s, payload={"cmd": f"delslot_{s}"}))
        kb.row()
    kb.add(Text("🔙 Назад", payload={"cmd": "back_to_schedule"}))
    await event.edit_message("Выберите слот для удаления:", keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorScheduleStates.delete_slot)


async def confirm_del_slot(event: MessageEvent):
    slot = event.payload["cmd"].split("_", 1)[1]
    data = await state_dispenser.get(event.user_id)
    tid = data["tid"]
    day = data["current_day"]
    await delete_schedule_slot(tid, day, slot)
    await event.edit_message("Слот удалён.")
    sched = await get_schedule(tid)
    slots = sched.get(day, [])
    text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + "\n".join(f"• {s}" for s in slots) if slots else "Нет слотов."
    kb = Keyboard(inline=True)
    kb.add(Text("➕ Добавить слот", payload={"cmd": "add_slot"}))
    kb.row()
    kb.add(Text("📅 Заполнить промежуток", payload={"cmd": "add_range"}))
    kb.row()
    if slots:
        kb.add(Text("❌ Удалить слот", payload={"cmd": "del_slot"}))
        kb.row()
    kb.add(Text("🔙 К дням недели", payload={"cmd": "back_to_schedule"}))
    await event.edit_message(text, keyboard=kb.get_json())
    await state_dispenser.set(event.user_id, TutorScheduleStates.manage_day_slots)

# Статистика преподавателя

async def tutor_stats_menu(event: MessageEvent):
    tid = int(event.payload["cmd"].split("_")[2])
    fin = await get_tutor_financials(tid)
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    comm_percent = tutor.get("commission_percent", 15) if tutor else 15
    text = (
        f"📊 Статистика за всё время\n"
        f"• Проведено занятий: {fin['total_lessons']}\n"
        f"• Общий доход: {fin['total_income']:.2f} руб.\n"
        f"• Комиссия ({comm_percent}%{', авто' if tutor.get('commission_mode')=='auto' else ''}): {fin['commission_amount']:.2f} руб.\n"
        f"• Доход после комиссии: {fin['net_income']:.2f} руб.\n\n"
        "Выберите месяц для детализации:"
    )
    now = datetime.now()
    months = sorted(set((d.year, d.month) for d in [now - timedelta(days=30 * i) for i in range(12)]), reverse=True)
    kb = Keyboard(inline=True)
    for y, m in months:
        kb.add(Text(f"{y}-{m:02d}", payload={"cmd": f"tutor_stats_month_{tid}_{y}_{m}"}))
        kb.row()
    kb.add(Text("🔙 Назад", payload={"cmd": f"back_to_tutor_panel_{tid}"}))
    await event.edit_message(text, keyboard=kb.get_json())


async def tutor_stats_month(event: MessageEvent):
    parts = event.payload["cmd"].split("_")
    tid = int(parts[3])
    year = int(parts[4])
    month = int(parts[5])
    fin = await get_tutor_financials(tid, year, month)
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    comm_percent = tutor.get("commission_percent", 15) if tutor else 15
    text = (
        f"📊 Статистика за {year}-{month:02d}\n"
        f"• Проведено занятий: {fin['total_lessons']}\n"
        f"• Доход: {fin['total_income']:.2f} руб.\n"
        f"• Комиссия ({comm_percent}%): {fin['commission_amount']:.2f} руб.\n"
        f"• Доход после комиссии: {fin['net_income']:.2f} руб."
    )
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 К общей статистике", payload={"cmd": f"tutor_stats_{tid}"}))
    await event.edit_message(text, keyboard=kb.get_json())

# ==================== ПОМОЩЬ ====================
@bot.on.private_message(text="❓ Помощь")
async def help(message: Message):
    help_text = (
        "📖 Помощь по использованию бота\n\n"
        "👤 Для учеников\n"
        "• Информация о репетиторах – узнайте об образовании, опыте, предметах и стоимости занятий.\n"
        "• Информация о занятиях – формат, длительность, скидки.\n"
        "• Запись на занятие – выберите преподавателя, предмет, дату и время из доступных слотов.\n"
        "• Мои записи – список активных занятий. Можно отменить или перенести запись (не позднее 24 часов).\n"
        "• Оплата – по QR-коду, карте или СБП.\n"
        "• Связь с преподавателем – напишите сообщение конкретному преподавателю.\n"
        "• Поддержка – задайте вопрос администратору.\n\n"
        "👨‍🏫 Для преподавателей\n"
        "• Мои ученики – список записей. Подтверждайте, отклоняйте, отменяйте или переносите занятия.\n"
        "• Настроить расписание – укажите рабочие дни и временные слоты.\n"
        "• Связь с учеником – напишите ученику напрямую.\n"
        "• Статистика – общая и помесячная информация о занятиях, доходе и комиссии.\n\n"
        "⏰ Напоминания за час до начала занятия получают и ученик, и преподаватель.\n\n"
        "⚠️ Важные правила\n"
        "• Отмена и перенос занятия возможны не позднее чем за 24 часа.\n"
        "• Для возврата в главное меню используйте кнопку «Назад в меню» или команду «Начать».\n"
        "• Если у вас нет доступа к нужному разделу, обратитесь в поддержку."
    )
    kb = Keyboard(inline=True)
    kb.add(Text("🔙 Назад в меню", payload={"cmd": "back_to_menu"}))
    await message.answer(help_text, keyboard=kb.get_json())

# ==================== Обработка email для платежа ====================
@bot.on.private_message()
async def process_payment_email(message: Message):
    # Срабатывает на любое текстовое сообщение, если пользователь ожидает ввода email
    booking_id = await get_pending_email_request(message.from_id)
    if not booking_id:
        return  # не в контексте платежа — игнорируем
    email = message.text.strip()
    if "@" not in email or "." not in email:
        return  # не email — ничего не делаем

    await set_user_email(message.from_id, email)
    bookings = await get_all_bookings()
    booking = bookings.get(booking_id)
    if not booking:
        await message.answer("Ошибка: запись не найдена.")
        await delete_pending_email_request(message.from_id)
        return

    await create_and_send_payment(message, booking, email, booking_id)
    await delete_pending_email_request(message.from_id)

# ==================== Запуск ====================

async def main():
    await init_db()
    # Запуск фоновых задач
    asyncio.create_task(periodic_cleanup())
    asyncio.create_task(reminder_loop())
    asyncio.create_task(pending_reminder_loop())
    await bot.run_polling()

async def periodic_cleanup():
    while True:
        await cleanup_old_bookings()
        await check_pending_payments()
        await asyncio.sleep(3600)

async def check_pending_payments():
    bookings = await get_all_bookings()
    for bid, b in bookings.items():
        if b["status"] != "confirmed" or not b.get("tinkoff_payment_id"):
            continue
        payment_state = await check_payment(b["tinkoff_payment_id"])
        if payment_state.get("Success") and payment_state.get("Status") in ("CONFIRMED", "AUTHORIZED"):
            await update_booking(bid, status="paid")
            await bot.api.messages.send(user_id=b["user_id"], message="✅ Оплата получена! Занятие подтверждено.", random_id=0)
            await add_lesson_to_balance(b["user_id"], b["tutor_id"], b["subject"])
            tutors = await get_all_tutors()
            tutor = tutors.get(b["tutor_id"])
            if tutor and tutor.get("vk_id"):
                await bot.api.messages.send(user_id=tutor["vk_id"],
                                            message=f"✅ Оплата за занятие {b['date']} {b['time_slot']} получена.",
                                            random_id=0)
        elif payment_state.get("Status") in ("REJECTED", "CANCELED"):
            await update_booking(bid, status="cancelled")
            await bot.api.messages.send(user_id=b["user_id"], message="❌ Платёж не прошёл. Запись отменена.", random_id=0)

async def send_reminders():
    now = datetime.now()
    bookings = await get_all_bookings()
    for bid, b in bookings.items():
        if b.get("status") != "confirmed" or b.get("reminded"):
            continue
        try:
            date_str = b["date"]
            start_time_str = b["time_slot"].split("-")[0]
            dt = datetime.strptime(date_str + " " + start_time_str, "%d.%m.%Y %H:%M")
        except ValueError:
            continue
        diff = dt - now
        if timedelta(minutes=59) < diff <= timedelta(hours=1):
            student_id = b["user_id"]
            tutor_id = b["tutor_id"]
            tutors = await get_all_tutors()
            tutor_name = tutors.get(tutor_id, {}).get("name", "Преподаватель")

            student_msg = (
                f"⏰ Напоминание! Через час у вас занятие по предмету «{b['subject']}» "
                f"с преподавателем {tutor_name}. Время: {b['date']} {b['time_slot']}"
            )
            await bot.api.messages.send(user_id=student_id, message=student_msg, random_id=0)

            tutor = tutors.get(tutor_id)
            if tutor and tutor.get("vk_id"):
                tutor_msg = (
                    f"⏰ Напоминание! Через час у вас занятие по предмету «{b['subject']}» "
                    f"с учеником {b['username']} (ID: {student_id}). Время: {b['date']} {b['time_slot']}"
                )
                try:
                    await bot.api.messages.send(user_id=tutor["vk_id"], message=tutor_msg, random_id=0)
                except:
                    pass

            await update_booking(bid, reminded=1)

async def reminder_loop():
    while True:
        await send_reminders()
        await asyncio.sleep(60)

async def send_pending_reminders():
    bookings = await get_all_bookings()
    pending_by_tutor = {}
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for bid, b in bookings.items():
        if b["status"] != "pending":
            continue
        try:
            booking_date = datetime.strptime(b["date"], "%d.%m.%Y")
            if booking_date < today:
                continue
        except ValueError:
            continue
        pending_by_tutor.setdefault(b["tutor_id"], []).append(b)

    tutors = await get_all_tutors()
    for tid, plist in pending_by_tutor.items():
        tutor = tutors.get(tid)
        if not tutor or not tutor.get("vk_id"):
            continue
        lines = [f"🔔 У вас есть неподтверждённые заявки ({len(plist)}):"]
        for b in plist:
            lines.append(f"• {b['username']}: {b['subject']}, {b['date']} {b['time_slot']}")
        text = "\n".join(lines)
        keyboard = Keyboard(inline=True)
        keyboard.add(Text("📋 Мои ученики", payload={"cmd": f"tutor_students_{tid}"}))
        try:
            await bot.api.messages.send(user_id=tutor["vk_id"], message=text, keyboard=keyboard.get_json(), random_id=0)
        except Exception as e:
            logging.warning(f"Не удалось отправить напоминание преподавателю {tid}: {e}")


# ==================== УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК CALLBACK-КОМАНД ====================
@bot.on.raw_event()
async def universal_raw_event(event: dict):
    logging.info(f"Получено сырое событие: {type(event)} - {event}")
    # Это сырой обработчик всех событий, приходящих от Long Poll
    # Интересуют только сообщения и события callback-кнопок
    if isinstance(event, Message):
        # Обычные сообщения обрабатываются другими хендлерами, здесь ничего не делаем
        return
    # Попытка опознать MessageEvent вручную
    if not hasattr(event, 'payload'):
        return
    cmd = getattr(event, 'payload', {}).get('cmd', '')
    if not cmd:
        return
    user_id = getattr(event, 'user_id', None)
    if not user_id:
        return

    user_id = event.user_id

    # --- Навигация и общие действия ---
    if cmd == "back_to_menu":
        await state_dispenser.delete(user_id)
        await event.edit_message("Главное меню", keyboard=await get_main_menu(user_id))

    elif cmd == "back_to_tutors":
        await back_to_tutors(event)

    elif cmd == "tutor_info":
        await show_tutor_info(event)

    # --- Пробное занятие ---
    elif cmd == "trials":
        await start_trials_booking(event)
    elif cmd == "trial_subject":
        await trial_subject_chosen(event)
    elif cmd == "trial_date":
        await trial_date_chosen(event)
    elif cmd == "back_to_trial_dates":
        await back_to_trial_dates(event)
    elif cmd == "trial_slot":
        await trial_slot_chosen(event)
    elif cmd == "confirm_trial":
        await confirm_trial_booking(event)

    # --- Запись на занятие ---
    elif cmd == "tutor_booking":
        await choose_tutor_booking(event)
    elif cmd == "back_to_tutors_booking":
        await back_to_tutors_booking(event)
    elif cmd.startswith("subject_"):
        await subject_chosen(event)
    elif cmd.startswith("date_"):
        await choose_date(event)
    elif cmd == "back_to_date":
        await back_to_date(event)
    elif cmd.startswith("slot_"):
        await choose_slot(event)
    elif cmd == "confirm_booking":
        await confirm_booking(event)
    elif cmd == "cancel_booking":
        await cancel_booking(event)

    # --- Мои записи (ученик) ---
    elif cmd.startswith("cancel_student_"):
        await cancel_student_booking(event)
    elif cmd == "student_stats":
        await show_student_stats(event)
    elif cmd == "back_to_my_records":
        await back_to_my_records(event)

    # --- Перенос учеником ---
    elif cmd.startswith("reschedule_student_"):
        await student_reschedule_start(event)
    elif cmd.startswith("reschedule_date_"):
        await student_reschedule_date(event)
    elif cmd == "back_to_reschedule_date":
        await back_to_reschedule_date(event)
    elif cmd.startswith("reschedule_slot_"):
        await student_reschedule_slot(event)
    elif cmd == "confirm_student_reschedule":
        await confirm_student_reschedule(event)

    # --- Оплата ---
    elif cmd == "back_to_pay":
        await back_to_pay(event)
    elif cmd == "qr":
        await qr(event)
    elif cmd == "card":
        await card(event)
    elif cmd == "sbp":
        await sbp(event)

    # --- Учебные материалы ---
    elif cmd == "back_to_mat":
        await back_to_mat(event)
    elif cmd == "book":
        await book(event)
    elif cmd == "vid":
        await vid(event)
    elif cmd == "bookh":
        await bookh(event)
    elif cmd == "bookf":
        await bookf(event)
    elif cmd == "videh":
        await videh(event)
    elif cmd == "videf":
        await videf(event)

    # --- Связь с преподавателем ---
    elif cmd.startswith("msg_tutor_"):
        await choose_msg_tutor(event)
    elif cmd == "cancel_msg_to_tutor":
        await cancel_msg_to_tutor(event)
    elif cmd.startswith("reply_"):
        await process_reply_button(event)

    # --- Связь преподавателя с учеником ---
    elif cmd.startswith("tutorcontactstudent_"):
        await tutor_contact_student_chosen(event)
    elif cmd == "cancel_tutor_msg_to_student":
        await cancel_tutor_msg_to_student(event)

    # --- Поддержка ---
    elif cmd == "cancel_support":
        await cancel_support(event)
    elif cmd.startswith("support_reply_"):
        await support_reply_start(event)

    # --- Админ-панель ---
    elif cmd == "admin_add":
        await admin_add_start(event)
    elif cmd == "admin_panel_open":
        await open_admin_panel(event)
    elif cmd == "admin_edit_list":
        await admin_edit_list(event)
    elif cmd.startswith("edit_tutor_"):
        await edit_tutor_choice(event)
    elif cmd.startswith("edit_"):
        await edit_field_choice(event)
    elif cmd == "manage_subjects":
        await manage_subjects(event)
    elif cmd == "back_to_edit_tutor":
        await back_to_edit_tutor(event)
    elif cmd == "add_subject":
        await add_subject_start(event)
    elif cmd.startswith("editsubj_"):
        await edit_subject_menu(event)
    elif cmd == "editsubj_name":
        await edit_subject_name_start(event)
    elif cmd == "editsubj_price":
        await edit_subject_price_start(event)
    elif cmd == "editsubj_delete":
        await delete_subject_confirm(event)
    elif cmd == "confirm_delete_subject":
        await confirm_delete_subject(event)
    elif cmd == "back_to_subjects_list":
        await back_to_subjects_list(event)
    elif cmd == "toggle_commission_mode":
        await toggle_commission_mode(event)
    elif cmd == "admin_delete_list":
        await admin_delete_list(event)
    elif cmd.startswith("del_tutor_"):
        await delete_tutor_confirm(event)
    elif cmd == "confirm_delete":
        await confirm_delete(event)
    elif cmd == "admin_stats":
        await admin_stats_menu(event)
    elif cmd == "admin_stats_tutors":
        await admin_stats_tutors_overview(event)
    elif cmd.startswith("admin_stats_tutors_month_"):
        await admin_stats_tutors_month(event)
    elif cmd == "admin_stats_students":
        await admin_stats_students(event)
    elif cmd == "add_another_subject":
        await add_another_subject(event)
    elif cmd == "finish_adding_subjects":
        await finish_adding_subjects(event)

    # --- Панель преподавателя ---
    elif cmd.startswith("tutor_profile_"):
        await show_tutor_own_profile(event)
    elif cmd.startswith("back_to_tutor_panel_"):
        await back_to_tutor_panel(event)
    elif cmd.startswith("tutor_students_"):
        await show_students(event)
    elif cmd.startswith("tutor_confirm_"):
        await tutor_confirm_booking(event)
    elif cmd.startswith("tutor_reject_"):
        await tutor_reject_booking(event)
    elif cmd.startswith("tutor_cancel_"):
        await tutor_cancel_booking(event)
    elif cmd.startswith("tutor_reschedule_"):
        await tutor_reschedule_start(event)
    elif cmd.startswith("t_reschedule_date_"):
        await tutor_reschedule_date(event)
    elif cmd == "back_tutor_reschedule_date":
        await back_tutor_reschedule_date(event)
    elif cmd.startswith("t_reschedule_slot_"):
        await tutor_reschedule_slot(event)
    elif cmd == "confirm_tutor_reschedule":
        await confirm_tutor_reschedule(event)

    # --- Настройка расписания преподавателем ---
    elif cmd.startswith("tutor_schedule_"):
        await schedule_main(event)
    elif cmd.startswith("sched_day_"):
        await edit_day(event)
    elif cmd == "back_to_schedule":
        await back_to_schedule(event)
    elif cmd.startswith("block_day_"):
        await handle_block_day(event)
    elif cmd.startswith("unblock_day_"):
        await handle_unblock_day(event)
    elif cmd == "add_slot":
        await add_slot_start(event)
    elif cmd == "add_range":
        await add_range_start(event)
    elif cmd.startswith("dur_"):
        await range_duration_chosen(event)
    elif cmd == "add_range_back":
        await range_break_back(event)
    elif cmd.startswith("brk_"):
        await range_break_chosen(event)
    elif cmd == "del_slot":
        await del_slot_start(event)
    elif cmd.startswith("delslot_"):
        await confirm_del_slot(event)

    # --- Статистика преподавателя ---
    elif cmd.startswith("tutor_stats_month_"):
        await tutor_stats_month(event)
    elif cmd.startswith("tutor_stats_"):
        await tutor_stats_menu(event)

    # Если команда не распознана, ничего не делаем (можно добавить логирование)


async def pending_reminder_loop():
    msk = timezone(timedelta(hours=3))
    while True:
        now = datetime.now(msk)
        if now.hour in (9, 15, 21) and now.minute == 0:
            await send_pending_reminders()
        await asyncio.sleep(60)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
