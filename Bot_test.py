import asyncio
import logging
import sys
import json
import os
from datetime import datetime, timedelta
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram import Bot, Dispatcher, html, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, StateFilter
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, ReplyKeyboardRemove, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
)

ADMING_ID = 846400165
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Передайте его через export BOT_TOKEN=...")

dp = Dispatcher()

TUTORS_FILE = "tutors.json"
SCHEDULES_FILE = "schedules.json"
BOOKINGS_FILE = "bookings.json"

tutors = {}
schedules = {}
bookings = {}

# ---- Загрузка данных ----
def load_tutors():
    global tutors
    if os.path.exists(TUTORS_FILE):
        with open(TUTORS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            tutors = {int(k): v for k, v in data.items()}
    else:
        tutors = {
            1: {
                "name": "Никита Тимурович",
                "photo": "",
                "telegram_id": None,
                "description": (
                    "Приветствую, меня зовут Никита Тимурович Ганжа кратко расскажу о себе. "
                    "Учусь в РНИМУ им. Пирогова на 4 курсе, в основном на отлично (на одной сессии была четверка). "
                    "Опыт преподавания имеется, вел курсы по химии в школе. Химией занимаюсь с 8 класса, два раза был "
                    "призером регионального этапа по химии, 99 баллов на ЕГЭ. Хорошо разбираюсь в физике, биологии, "
                    "математике и истории. Работаю с учениками на фундаментальное понимание химии, а не «тут нужно "
                    "просто выучить». Объясняю на примерах из жизни, из человеческого организма и природы. "
                    "Если вам кажется, что вы совсем не знаете химию, то я изменю это уже к 4 занятию. "
                    "Первое пробное занятие 30 минут, бесплатно."
                ),
                "subjects": {"Химия": 2500, "Физика": 1500}
            },
            2: {
                "name": "Юлия Евгеньевна",
                "photo": "",
                "telegram_id": None,
                "description": "Приветствую, меня зовут Юлия Евгеньевна Паймурзова...\n\nХотите записаться на пробное занятие?",
                "subjects": {"Химия": 1500}
            },
            3: {
                "name": "Никита Дмитриевич",
                "photo": "",
                "telegram_id": None,
                "description": "Приветствую, меня зовут Никита Дмитриевич Колебаев...\n\nХотите записаться на пробное занятие?",
                "subjects": {"Физика": 2500, "Математика": 2500, "Информатика": 2500}
            }
        }
        save_tutors()

def load_schedules():
    global schedules
    if os.path.exists(SCHEDULES_FILE):
        with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
            schedules = {int(k): v for k, v in json.load(f).items()}
    else:
        schedules = {}

def load_bookings():
    global bookings
    if os.path.exists(BOOKINGS_FILE):
        with open(BOOKINGS_FILE, "r", encoding="utf-8") as f:
            bookings = {int(k): v for k, v in json.load(f).items()}
    else:
        bookings = {}

def save_tutors():
    with open(TUTORS_FILE, "w", encoding="utf-8") as f:
        json.dump(tutors, f, ensure_ascii=False, indent=2)

def save_schedules():
    with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)

def save_bookings():
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(bookings, f, ensure_ascii=False, indent=2)

def get_next_tutor_id():
    if not tutors:
        return 1
    return max(tutors.keys()) + 1

def get_next_booking_id():
    if not bookings:
        return 1
    return max(bookings.keys()) + 1

# ---- Дни недели ----
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_NAMES = {
    "monday": "Пн", "tuesday": "Вт", "wednesday": "Ср",
    "thursday": "Чт", "friday": "Пт", "saturday": "Сб", "sunday": "Вс"
}

# ---- FSM состояния ----
class BookingStates(StatesGroup):
    choosing_tutor = State()
    choosing_subject = State()
    waiting_date = State()
    waiting_time = State()
    waiting_confirmation = State()

class ContactStates(StatesGroup):
    choosing_tutor = State()
    waiting_message = State()
    waiting_reply = State()

class AdminStates(StatesGroup):
    waiting_name = State()
    waiting_photo = State()
    waiting_description = State()
    waiting_telegram_id = State()
    waiting_subject_name = State()
    waiting_subject_price = State()
    waiting_edit_choice = State()
    waiting_new_value = State()
    waiting_delete_confirm = State()
    managing_subjects = State()
    adding_subject_name = State()
    adding_subject_price = State()
    editing_subject_choice = State()
    editing_subject_name_state = State()
    editing_subject_price_state = State()
    deleting_subject_confirm = State()

class TutorScheduleStates(StatesGroup):
    choose_day = State()
    manage_day_slots = State()
    add_slot = State()
    add_range = State()      # для ввода промежутка
    delete_slot = State()

# -------------------- ГЛАВНОЕ МЕНЮ (динамическое) --------------------
def get_main_menu(user_id: int) -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру главного меню. Админ-кнопка только для ADMING_ID."""
    buttons = [
        [KeyboardButton(text="ℹ️ Информация о репетиторах")],
        [KeyboardButton(text="📚 Информация о занятиях")],
        [KeyboardButton(text="📝 Запись на занятие")],
        [KeyboardButton(text="💳 Оплата")],
        [KeyboardButton(text="📖 Учебные материалы")],
        [KeyboardButton(text="✉️ Связь с преподавателем")],
        [KeyboardButton(text="❓ Помощь")],
        [KeyboardButton(text="👨‍🏫 Панель преподавателя")],
    ]
    if user_id == ADMING_ID:
        buttons.append([KeyboardButton(text="👨‍🏫 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
def make_tutors_keyboard(callback_prefix: str, back_callback: str = "back_to_menu"):
    buttons = []
    for tid, tdata in tutors.items():
        buttons.append([InlineKeyboardButton(text=tdata["name"], callback_data=f"{callback_prefix}_{tid}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def make_subjects_keyboard(tutor_id: int, back_callback: str = "back_to_menuz"):
    tutor = tutors.get(tutor_id)
    if not tutor:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)]
        ])
    buttons = []
    for subj in tutor["subjects"]:
        buttons.append([InlineKeyboardButton(text=subj, callback_data=f"subject_{tutor_id}_{subj}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к репетиторам", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def show_manage_subjects_menu(update, state: FSMContext, tid: int):
    tutor = tutors[tid]
    text = f"Предметы репетитора «{tutor['name']}»:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    buttons = []
    for subj in tutor["subjects"]:
        buttons.append([InlineKeyboardButton(text=f"✏️ {subj}", callback_data=f"editsubj_{subj}")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить предмет", callback_data="add_subject")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к редактированию", callback_data="back_to_edit_tutor")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    if isinstance(update, types.Message):
        await update.answer(text, reply_markup=keyboard)
    elif isinstance(update, types.CallbackQuery):
        await update.message.edit_text(text, reply_markup=keyboard)
    await state.set_state(AdminStates.managing_subjects)

# ----- Работа с расписанием и слотами -----
def get_schedule(tutor_id: int):
    return schedules.get(tutor_id, {})

def get_available_slots(tutor_id: int, date_str: str) -> list:
    date = datetime.strptime(date_str, "%d.%m.%Y")
    day_name = WEEKDAYS[date.weekday()]
    schedule = get_schedule(tutor_id)
    if day_name not in schedule:
        return []
    all_slots = schedule[day_name]
    busy = []
    for b in bookings.values():
        if b["tutor_id"] == tutor_id and b["date"] == date_str and b["status"] == "active":
            busy.append(b["time_slot"])
    free = [s for s in all_slots if s not in busy]
    return free

def get_available_dates(tutor_id: int, days_ahead=30) -> list:
    today = datetime.now()
    available = []
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        date_str = d.strftime("%d.%m.%Y")
        free = get_available_slots(tutor_id, date_str)
        if free:
            available.append(date_str)
    return available

def split_into_slots(start_time: str, end_time: str, duration_min=90):
    """Разбивает промежуток на слоты длительностью duration_min минут."""
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
        current = slot_end
    return slots

# ==================== БАЗОВЫЕ ОБРАБОТЧИКИ ====================
@dp.message(Command("start"))
async def Start(message: Message) -> None:
    await message.answer(
        f"Привет, {html.bold(message.from_user.full_name)}! Я онлайн ассистент Никиты Тимуровича. Чем могу помочь?",
        reply_markup=get_main_menu(message.from_user.id)
    )

@dp.message(F.text.in_(["🔙 Назад"]))
async def main_menu_buttons(message: Message) -> None:
    await message.answer("Главное меню:", reply_markup=get_main_menu(message.from_user.id))

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    try:
        await call.message.delete()
    except:
        pass
    try:
        await call.message.answer("Главное меню:", reply_markup=get_main_menu(call.from_user.id))
    except:
        pass

# ==================== ИНФОРМАЦИЯ О РЕПЕТИТОРАХ ====================
@dp.message(F.text.in_(["ℹ️ Информация о репетиторах"]))
async def repet(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = make_tutors_keyboard("tutor_info")
    await message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_tutors")
async def back_to_tutors(call: CallbackQuery):
    keyboard = make_tutors_keyboard("tutor_info")
    if call.message.content_type == 'photo':
        await call.message.delete()
        await call.message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    else:
        await call.message.edit_text("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    await call.answer()

@dp.callback_query(F.data.startswith("tutor_info_"))
async def show_tutor_info(call: CallbackQuery):
    tid = int(call.data.split("_")[-1])
    tutor = tutors.get(tid)
    if not tutor:
        await call.answer("Репетитор не найден", show_alert=True)
        return
    text = tutor["description"] + "\n\nПредметы и цены:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_tutors")]
    ])
    if tutor["photo"]:
        await call.message.delete()
        await call.bot.send_photo(chat_id=call.message.chat.id, photo=tutor["photo"], caption=text, reply_markup=keyboard)
    else:
        await call.message.edit_text(text, reply_markup=keyboard)
    await call.answer()

# ==================== ИНФОРМАЦИЯ О ЗАНЯТИЯХ ====================
@dp.message(F.text.in_(["📚 Информация о занятиях"]))
async def lesson_info(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    subjects_set = set()
    for t in tutors.values():
        subjects_set.update(t["subjects"].keys())
    if not subjects_set:
        await message.answer("Пока нет доступных предметов.", reply_markup=get_main_menu(message.from_user.id))
        return
    buttons = [[InlineKeyboardButton(text=subj, callback_data=f"lesson_subject_{subj}")] for subj in sorted(subjects_set)]
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    await message.answer("Какой предмет Вас интересует?", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data == "back_to_lesson_subjects")
async def back_to_lesson_subjects(call: CallbackQuery):
    subjects_set = set()
    for t in tutors.values():
        subjects_set.update(t["subjects"].keys())
    buttons = [[InlineKeyboardButton(text=subj, callback_data=f"lesson_subject_{subj}")] for subj in sorted(subjects_set)]
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    await call.message.edit_text("Какой предмет Вас интересует?", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("lesson_subject_"))
async def show_lesson_subject_info(call: CallbackQuery):
    subject = call.data.split("lesson_subject_", 1)[1]
    lines = [f"Предмет: {subject}", ""]
    for t in tutors.values():
        if subject in t["subjects"]:
            lines.append(f"👨‍🏫 {t['name']} — {t['subjects'][subject]} руб.")
    await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К списку предметов", callback_data="back_to_lesson_subjects")]
    ]))
    await call.answer()

# ==================== ЗАПИСЬ НА ЗАНЯТИЕ (30 дней) ====================
@dp.message(F.text.in_(["📝 Запись на занятие"]))
async def zapis(message: types.Message, state: FSMContext):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = make_tutors_keyboard("tutor_booking", back_callback="back_to_menu")
    await message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    await state.clear()

@dp.callback_query(F.data.startswith("tutor_booking_"))
async def choose_tutor_booking(call: CallbackQuery, state: FSMContext):
    await call.answer()
    tid = int(call.data.split("_")[-1])
    tutor = tutors.get(tid)
    if not tutor:
        await call.message.edit_text("Ошибка выбора репетитора.")
        return
    await state.update_data(tutor_id=tid, tutor_name=tutor["name"])
    keyboard = make_subjects_keyboard(tid, back_callback="back_to_tutors_booking")
    await call.message.edit_text("На занятие по какому предмету вы хотите записаться?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_tutors_booking")
async def back_to_tutors_booking(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    keyboard = make_tutors_keyboard("tutor_booking", back_callback="back_to_menu")
    await call.message.edit_text("Кто из репетиторов Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("subject_"))
async def subject_chosen(call: CallbackQuery, state: FSMContext):
    await call.answer()
    parts = call.data.split("_", 2)
    if len(parts) < 3:
        return
    tid = int(parts[1])
    subject = parts[2]
    await state.update_data(subject=subject, tutor_id=tid)
    dates = get_available_dates(tid)  # по умолчанию 30 дней
    if not dates:
        await call.message.edit_text(
            "У этого преподавателя пока нет свободных дат.\nПопробуйте позже или свяжитесь с администратором.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к репетиторам", callback_data="back_to_tutors_booking")]
            ])
        )
        return
    buttons = []
    for d in dates:
        dt = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt.weekday()]]})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"date_{d}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к репетиторам", callback_data="back_to_tutors_booking")])
    await call.message.edit_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(BookingStates.waiting_date)

@dp.callback_query(F.data.startswith("date_"), StateFilter(BookingStates.waiting_date))
async def choose_date(call: CallbackQuery, state: FSMContext):
    await call.answer()
    date_str = call.data.split("_", 1)[1]
    await state.update_data(date=date_str)
    data = await state.get_data()
    tid = data["tutor_id"]
    slots = get_available_slots(tid, date_str)
    if not slots:
        await call.message.edit_text("На эту дату нет свободного времени.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К выбору даты", callback_data="back_to_date")]
        ]))
        return
    buttons = [[InlineKeyboardButton(text=s, callback_data=f"slot_{s}")] for s in slots]
    buttons.append([InlineKeyboardButton(text="🔙 К выбору даты", callback_data="back_to_date")])
    await call.message.edit_text("Выберите время:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(BookingStates.waiting_time)

@dp.callback_query(F.data == "back_to_date", StateFilter("*"))
async def back_to_date(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tid = data.get("tutor_id")
    dates = get_available_dates(tid)
    buttons = []
    for d in dates:
        dt = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt.weekday()]]})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"date_{d}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к репетиторам", callback_data="back_to_tutors_booking")])
    await call.message.edit_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(BookingStates.waiting_date)

@dp.callback_query(F.data.startswith("slot_"), StateFilter(BookingStates.waiting_time))
async def choose_slot(call: CallbackQuery, state: FSMContext):
    await call.answer()
    slot = call.data.split("_", 1)[1]
    await state.update_data(time_slot=slot)
    data = await state.get_data()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить запись", callback_data="confirm_booking")],
        [InlineKeyboardButton(text="✏️ Изменить время", callback_data="back_to_date")],
        [InlineKeyboardButton(text="❌ Отменить запись", callback_data="cancel_booking")]
    ])
    await call.message.edit_text(
        f"Проверьте данные:\n"
        f"👨‍🏫 Репетитор: {data['tutor_name']}\n"
        f"📚 Предмет: {data['subject']}\n"
        f"📅 Дата: {data['date']}\n"
        f"🕒 Время: {slot}\n\nВсё верно?",
        reply_markup=keyboard
    )
    await state.set_state(BookingStates.waiting_confirmation)

@dp.callback_query(F.data == "confirm_booking", StateFilter(BookingStates.waiting_confirmation))
async def confirm_booking(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    data = await state.get_data()
    tid = data["tutor_id"]
    tutor_name = data["tutor_name"]
    subject = data["subject"]
    date = data["date"]
    slot = data["time_slot"]
    user = call.from_user
    username = user.username or user.full_name
    uid = user.id

    new_id = get_next_booking_id()
    bookings[new_id] = {
        "tutor_id": tid,
        "user_id": uid,
        "username": username,
        "subject": subject,
        "date": date,
        "time_slot": slot,
        "status": "active"
    }
    save_bookings()

    booking_msg = (
        f"📝 Новая запись на занятие!\n"
        f"👤 Ученик: {username} (ID: {uid})\n"
        f"👨‍🏫 Репетитор: {tutor_name}\n"
        f"📚 Предмет: {subject}\n"
        f"📅 Дата: {date}\n"
        f"🕒 Время: {slot}"
    )
    await bot.send_message(ADMING_ID, booking_msg)
    tutor = tutors.get(tid)
    if tutor and tutor.get("telegram_id"):
        try:
            await bot.send_message(tutor["telegram_id"], booking_msg)
        except:
            pass

    await call.message.edit_text("✅ Запись успешно подтверждена! Преподаватель свяжется с вами.")
    await call.message.answer("Вы записаны на занятие. Ожидайте подтверждения от преподавателя.", reply_markup=get_main_menu(call.from_user.id))
    await state.clear()

@dp.callback_query(F.data == "cancel_booking", StateFilter(BookingStates.waiting_confirmation))
async def cancel_booking(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Запись отменена. Возвращаемся в главное меню.")
    await state.clear()
    await call.message.answer("Главное меню:", reply_markup=get_main_menu(call.from_user.id))

# ==================== ОПЛАТА ====================
@dp.message(F.text.in_(["💳 Оплата"]))
async def oplata(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Оплата по QR-коду", callback_data="qr")],
        [InlineKeyboardButton(text="💳 Оплата банковской картой", callback_data="card")],
        [InlineKeyboardButton(text="📲 Перевод СБП по номеру телефона", callback_data="sbp")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Какой способ оплаты вам удобнее?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_pay")
async def back_to_pay(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Оплата по QR-коду", callback_data="qr")],
        [InlineKeyboardButton(text="💳 Оплата банковской картой", callback_data="card")],
        [InlineKeyboardButton(text="📲 Перевод СБП по номеру телефона", callback_data="sbp")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Какой способ оплаты вам удобнее?", reply_markup=keyboard)
    await call.answer()

@dp.callback_query(F.data == "qr")
async def qr(call: CallbackQuery):
    await call.message.edit_text("📱 Сканируйте QR-код для оплаты в приложении вашего банка", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_pay")]
    ]))
    await call.answer()

@dp.callback_query(F.data == "card")
async def card(call: CallbackQuery):
    await call.message.edit_text("💳 Переходите по ссылке и следуйте дальнейшим инструкциям", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_pay")]
    ]))
    await call.answer()

@dp.callback_query(F.data == "sbp")
async def sbp(call: CallbackQuery):
    await call.message.edit_text("📲 Перевод выполняйте, указывая предмет и дату занятия, по номеру 89035370929 на Т-банк", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_pay")]
    ]))
    await call.answer()

# ==================== УЧЕБНЫЕ МАТЕРИАЛЫ ====================
@dp.message(F.text.in_(["📖 Учебные материалы"]))
async def material(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📘 Учебные пособия", callback_data="book")],
        [InlineKeyboardButton(text="🎥 Авторские видео", callback_data="vid")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Вы ищете пособия или видео?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_mat")
async def back_to_mat(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📘 Учебные пособия", callback_data="book")],
        [InlineKeyboardButton(text="🎥 Авторские видео", callback_data="vid")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Вы ищете пособия или видео?", reply_markup=keyboard)

@dp.callback_query(F.data == "book")
async def book(call: CallbackQuery):
    await call.message.edit_text("📘 Учебники и таблицы", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Химия", callback_data="bookh")],
        [InlineKeyboardButton(text="⚛️ Физика", callback_data="bookf")],
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_mat")]
    ]))

@dp.callback_query(F.data == "vid")
async def vid(call: CallbackQuery):
    await call.message.edit_text("🎥 Видеоматериалы (записи реакций и явлений)", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Химия", callback_data="videh")],
        [InlineKeyboardButton(text="⚛️ Физика", callback_data="videf")],
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_mat")]
    ]))

# ==================== СВЯЗЬ С ПРЕПОДАВАТЕЛЕМ (ОБНОВЛЁННАЯ) ====================
@dp.message(F.text.in_(["✉️ Связь с преподавателем"]))
async def svyaz(message: types.Message, state: FSMContext):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = make_tutors_keyboard("msg_tutor", back_callback="back_to_menu")
    await message.answer("Выберите преподавателя, которому хотите написать:", reply_markup=keyboard)
    await state.set_state(ContactStates.choosing_tutor)

@dp.callback_query(F.data.startswith("msg_tutor_"), StateFilter(ContactStates.choosing_tutor))
async def choose_msg_tutor(call: CallbackQuery, state: FSMContext):
    await call.answer()
    tid = int(call.data.split("_")[-1])
    tutor = tutors.get(tid)
    if not tutor:
        await call.message.edit_text("Преподаватель не найден.")
        return
    await state.update_data(msg_tutor_id=tid, msg_tutor_name=tutor["name"])
    await call.message.edit_text(f"Вы пишете преподавателю {tutor['name']}.\nВведите ваше сообщение:")
    await state.set_state(ContactStates.waiting_message)

@dp.message(ContactStates.waiting_message)
async def send_message_to_tutor(message: Message, state: FSMContext, bot: Bot):
    user = message.from_user
    username = user.username or user.full_name
    data = await state.get_data()
    tid = data["msg_tutor_id"]
    tutor_name = data["msg_tutor_name"]
    text = message.text.strip()

    # Сохраняем ID ученика для ответа
    await state.update_data(student_id=user.id, student_username=username)

    forward_msg = (
        f"📨 Сообщение от ученика\n"
        f"👤 {username} (ID: {user.id})\n"
        f"✉️ Преподавателю: {tutor_name}\n\n"
        f"💬 Текст:\n{text}"
    )

    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Ответить", callback_data=f"reply_{user.id}")]
    ])
    await bot.send_message(ADMING_ID, forward_msg, reply_markup=reply_markup)
    tutor = tutors.get(tid)
    if tutor and tutor.get("telegram_id"):
        try:
            await bot.send_message(tutor["telegram_id"], forward_msg, reply_markup=reply_markup)
        except:
            pass

    await message.answer("✅ Сообщение отправлено. Ожидайте ответа.", reply_markup=get_main_menu(message.from_user.id))
    await state.clear()

@dp.callback_query(F.data.startswith("reply_"))
async def process_reply_button(call: CallbackQuery, state: FSMContext):
    await call.answer()
    student_id = int(call.data.split("_")[1])
    await state.update_data(reply_student_id=student_id)
    await call.message.answer("Введите ваш ответ (текст):")
    await state.set_state(ContactStates.waiting_reply)

@dp.message(ContactStates.waiting_reply)
async def send_reply_to_student(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    student_id = data["reply_student_id"]
    reply_text = f"📬 Ответ от преподавателя/администратора:\n{message.text}"
    try:
        await bot.send_message(student_id, reply_text)
        await message.answer("✅ Ответ отправлен ученику.", reply_markup=get_main_menu(message.from_user.id))
    except:
        await message.answer("⚠️ Не удалось отправить ответ (возможно, ученик заблокировал бота).", reply_markup=get_main_menu(message.from_user.id))
    await state.clear()

# ==================== АДМИН-ПАНЕЛЬ (только для вас) ====================
@dp.message(F.text.in_(["👨‍🏫 Админ-панель"]))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMING_ID:
        await message.answer("⛔ Доступ запрещён.")
        return
    await message.answer("Админ-панель управления репетиторами", reply_markup=ReplyKeyboardRemove())
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить репетитора", callback_data="admin_add")],
        [InlineKeyboardButton(text="✏️ Редактировать репетитора", callback_data="admin_edit_list")],
        [InlineKeyboardButton(text="❌ Удалить репетитора", callback_data="admin_delete_list")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Выберите действие:", reply_markup=keyboard)

# --- ДОБАВЛЕНИЕ РЕПЕТИТОРА (с Telegram ID) ---
@dp.callback_query(F.data == "admin_add")
async def admin_add_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Введите имя репетитора:")
    await state.set_state(AdminStates.waiting_name)

@dp.message(AdminStates.waiting_name)
async def admin_add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("Отправьте фото репетитора (или напишите 'нет', чтобы пропустить):")
    await state.set_state(AdminStates.waiting_photo)

@dp.message(AdminStates.waiting_photo)
async def admin_add_photo(message: Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        await state.update_data(photo=file_id)
    else:
        await state.update_data(photo="")
    await message.answer("Введите описание репетитора:")
    await state.set_state(AdminStates.waiting_description)

@dp.message(AdminStates.waiting_description)
async def admin_add_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await message.answer("Введите Telegram ID репетитора (число) или 0, если нет:")
    await state.set_state(AdminStates.waiting_telegram_id)

@dp.message(AdminStates.waiting_telegram_id)
async def admin_add_telegram_id(message: Message, state: FSMContext):
    try:
        tid_val = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число или 0.")
        return
    await state.update_data(telegram_id=tid_val if tid_val != 0 else None)
    await state.update_data(subjects={})
    await message.answer("Введите название первого предмета, который ведёт репетитор:")
    await state.set_state(AdminStates.waiting_subject_name)

@dp.message(AdminStates.waiting_subject_name)
async def admin_add_subject_name(message: Message, state: FSMContext):
    subject = message.text.strip()
    await state.update_data(temp_subject=subject)
    await message.answer(f"Введите цену за занятие для предмета «{subject}» (целое число рублей):")
    await state.set_state(AdminStates.waiting_subject_price)

@dp.message(AdminStates.waiting_subject_price)
async def admin_add_subject_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip())
    except ValueError:
        await message.answer("Пожалуйста, введите целое число.")
        return
    data = await state.get_data()
    subjects = data.get("subjects", {})
    temp_subject = data.get("temp_subject")
    subjects[temp_subject] = price
    await state.update_data(subjects=subjects)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, добавить ещё", callback_data="add_another_subject")],
        [InlineKeyboardButton(text="❌ Нет, закончить", callback_data="finish_adding_subjects")]
    ])
    await message.answer(f"Предмет «{temp_subject}» с ценой {price} руб. добавлен. Добавить ещё предмет?", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_subject_name)

@dp.callback_query(F.data == "add_another_subject", StateFilter(AdminStates.waiting_subject_name))
async def add_another_subject(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Введите название следующего предмета:")
    await state.set_state(AdminStates.waiting_subject_name)

@dp.callback_query(F.data == "finish_adding_subjects", StateFilter(AdminStates.waiting_subject_name))
async def finish_adding_subjects(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    new_id = get_next_tutor_id()
    tutors[new_id] = {
        "name": data["name"],
        "photo": data.get("photo", ""),
        "telegram_id": data.get("telegram_id"),
        "description": data["description"],
        "subjects": data["subjects"]
    }
    save_tutors()
    # Добавляем кнопку "Назад"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text(f"✅ Репетитор «{data['name']}» успешно добавлен (ID {new_id}).", reply_markup=keyboard)
    await state.clear()

# --- РЕДАКТИРОВАНИЕ РЕПЕТИТОРА ---
@dp.callback_query(F.data == "admin_edit_list")
async def admin_edit_list(call: CallbackQuery):
    await call.answer()
    if not tutors:
        await call.message.edit_text("Нет репетиторов для редактирования.")
        return
    keyboard = make_tutors_keyboard("edit_tutor", back_callback="back_to_menu")
    await call.message.edit_text("Выберите репетитора для редактирования:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("edit_tutor_"))
async def edit_tutor_choice(call: CallbackQuery, state: FSMContext):
    await call.answer()
    tid = int(call.data.split("_")[-1])
    await state.update_data(edit_tutor_id=tid)
    tutor = tutors[tid]
    info = f"Редактирование: {tutor['name']}\n\nЧто хотите изменить?"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить имя", callback_data="edit_name")],
        [InlineKeyboardButton(text="Изменить описание", callback_data="edit_desc")],
        [InlineKeyboardButton(text="Изменить фото", callback_data="edit_photo")],
        [InlineKeyboardButton(text="Изменить Telegram ID", callback_data="edit_telegram_id")],
        [InlineKeyboardButton(text="📚 Управление предметами", callback_data="manage_subjects")],
        [InlineKeyboardButton(text="🔙 К списку", callback_data="admin_edit_list")]
    ])
    await call.message.edit_text(info, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("edit_"), StateFilter("*"))
async def edit_field_choice(call: CallbackQuery, state: FSMContext):
    await call.answer()
    field = call.data.split("_", 1)[1]  # name, desc, photo, telegram_id
    await state.update_data(edit_field=field)
    prompts = {
        "name": "Введите новое имя:",
        "desc": "Введите новое описание:",
        "photo": "Отправьте новое фото (или 'нет', чтобы пропустить):",
        "telegram_id": "Введите новый Telegram ID (число или 0, чтобы удалить):"
    }
    await call.message.edit_text(prompts.get(field, "Введите новое значение:"))
    await state.set_state(AdminStates.waiting_new_value)

@dp.message(AdminStates.waiting_new_value)
async def process_new_value(message: Message, state: FSMContext):
    data = await state.get_data()
    tid = data["edit_tutor_id"]
    field = data["edit_field"]
    if field == "photo":
        if message.photo:
            file_id = message.photo[-1].file_id
            tutors[tid]["photo"] = file_id
        else:
            tutors[tid]["photo"] = ""
    elif field == "name":
        tutors[tid]["name"] = message.text.strip()
    elif field == "desc":
        tutors[tid]["description"] = message.text.strip()
    elif field == "telegram_id":
        try:
            new_id = int(message.text.strip())
            tutors[tid]["telegram_id"] = new_id if new_id != 0 else None
        except ValueError:
            await message.answer("Введите целое число или 0.")
            return
    save_tutors()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("✅ Изменения сохранены.", reply_markup=keyboard)
    await state.clear()
# --- Управление предметами (полный код) ---
@dp.callback_query(F.data == "manage_subjects", StateFilter("*"))
async def manage_subjects(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    if not tid or tid not in tutors:
        await call.answer("Ошибка", show_alert=True)
        return
    await show_manage_subjects_menu(call, state, tid)

@dp.callback_query(F.data == "back_to_edit_tutor", StateFilter(AdminStates.managing_subjects))
async def back_to_edit_tutor(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    if not tid or tid not in tutors:
        await call.answer("Ошибка", show_alert=True)
        return
    tutor = tutors[tid]
    info = f"Редактирование: {tutor['name']}\n\nЧто хотите изменить?"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить имя", callback_data="edit_name")],
        [InlineKeyboardButton(text="Изменить описание", callback_data="edit_desc")],
        [InlineKeyboardButton(text="Изменить фото", callback_data="edit_photo")],
        [InlineKeyboardButton(text="Изменить Telegram ID", callback_data="edit_telegram_id")],
        [InlineKeyboardButton(text="📚 Управление предметами", callback_data="manage_subjects")],
        [InlineKeyboardButton(text="🔙 К списку", callback_data="admin_edit_list")]
    ])
    await call.message.edit_text(info, reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_edit_choice)

@dp.callback_query(F.data == "add_subject", StateFilter(AdminStates.managing_subjects))
async def add_subject_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Введите название нового предмета:")
    await state.set_state(AdminStates.adding_subject_name)

@dp.message(AdminStates.adding_subject_name)
async def process_adding_subject_name(message: Message, state: FSMContext):
    name = message.text.strip()
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    if tid and name in tutors[tid]["subjects"]:
        await message.answer("Такой предмет уже существует. Введите другое название.")
        return
    await state.update_data(temp_new_subject=name)
    await message.answer(f"Введите цену за занятие для предмета «{name}» (целое число рублей):")
    await state.set_state(AdminStates.adding_subject_price)

@dp.message(AdminStates.adding_subject_price)
async def process_adding_subject_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число.")
        return
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    name = data.get("temp_new_subject")
    if tid and tid in tutors:
        tutors[tid]["subjects"][name] = price
        save_tutors()
    await message.answer(f"✅ Предмет «{name}» добавлен с ценой {price} руб.")
    await show_manage_subjects_menu(message, state, tid)

@dp.callback_query(F.data.startswith("editsubj_"), StateFilter(AdminStates.managing_subjects))
async def edit_subject_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    subj_name = call.data.split("_", 1)[1]
    await state.update_data(edit_subject_name=subj_name)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data="editsubj_name")],
        [InlineKeyboardButton(text="💰 Изменить цену", callback_data="editsubj_price")],
        [InlineKeyboardButton(text="❌ Удалить предмет", callback_data="editsubj_delete")],
        [InlineKeyboardButton(text="🔙 Назад к списку предметов", callback_data="back_to_subjects_list")],
    ])
    await call.message.edit_text(f"Предмет: {subj_name}\nВыберите действие:", reply_markup=keyboard)
    await state.set_state(AdminStates.editing_subject_choice)

@dp.callback_query(F.data == "back_to_subjects_list", StateFilter(AdminStates.editing_subject_choice))
async def back_to_subjects_list(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    await show_manage_subjects_menu(call, state, tid)

@dp.callback_query(F.data == "editsubj_name", StateFilter(AdminStates.editing_subject_choice))
async def edit_subject_name_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Введите новое название предмета:")
    await state.set_state(AdminStates.editing_subject_name_state)

@dp.message(AdminStates.editing_subject_name_state)
async def process_new_subject_name(message: Message, state: FSMContext):
    new_name = message.text.strip()
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    old_name = data.get("edit_subject_name")
    if tid and old_name in tutors[tid]["subjects"]:
        if new_name != old_name and new_name in tutors[tid]["subjects"]:
            await message.answer("Предмет с таким названием уже существует. Введите другое.")
            return
        tutors[tid]["subjects"][new_name] = tutors[tid]["subjects"].pop(old_name)
        save_tutors()
    await message.answer(f"✅ Название предмета изменено на «{new_name}».")
    await state.update_data(edit_subject_name=None)
    await show_manage_subjects_menu(message, state, tid)

@dp.callback_query(F.data == "editsubj_price", StateFilter(AdminStates.editing_subject_choice))
async def edit_subject_price_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Введите новую цену (целое число):")
    await state.set_state(AdminStates.editing_subject_price_state)

@dp.message(AdminStates.editing_subject_price_state)
async def process_new_subject_price(message: Message, state: FSMContext):
    try:
        new_price = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число.")
        return
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    subj = data.get("edit_subject_name")
    if tid and subj in tutors[tid]["subjects"]:
        tutors[tid]["subjects"][subj] = new_price
        save_tutors()
    await message.answer(f"✅ Цена для предмета «{subj}» изменена на {new_price} руб.")
    await show_manage_subjects_menu(message, state, tid)

@dp.callback_query(F.data == "editsubj_delete", StateFilter(AdminStates.editing_subject_choice))
async def delete_subject_confirm(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    subj = data.get("edit_subject_name")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete_subject")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_subjects_list")],
    ])
    await call.message.edit_text(f"Удалить предмет «{subj}»?", reply_markup=keyboard)
    await state.set_state(AdminStates.deleting_subject_confirm)

@dp.callback_query(F.data == "confirm_delete_subject", StateFilter(AdminStates.deleting_subject_confirm))
async def confirm_delete_subject(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    subj = data.get("edit_subject_name")
    if tid and subj in tutors[tid]["subjects"]:
        del tutors[tid]["subjects"][subj]
        save_tutors()
    await call.message.edit_text(f"✅ Предмет «{subj}» удалён.")
    await show_manage_subjects_menu(call, state, tid)

# --- УДАЛЕНИЕ РЕПЕТИТОРА ---
@dp.callback_query(F.data == "admin_delete_list")
async def admin_delete_list(call: CallbackQuery):
    await call.answer()
    if not tutors:
        await call.message.edit_text("Нет репетиторов для удаления.")
        return
    keyboard = make_tutors_keyboard("del_tutor", back_callback="back_to_menu")
    await call.message.edit_text("Выберите репетитора для удаления:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("del_tutor_"))
async def delete_tutor_confirm(call: CallbackQuery, state: FSMContext):
    await call.answer()
    tid = int(call.data.split("_")[-1])
    await state.update_data(del_tutor_id=tid)
    tutor = tutors[tid]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_delete_list")]
    ])
    await call.message.edit_text(f"Удалить репетитора «{tutor['name']}»?", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_delete_confirm)

@dp.callback_query(F.data == "confirm_delete", StateFilter(AdminStates.waiting_delete_confirm))
async def confirm_delete(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    tid = data["del_tutor_id"]
    name = tutors[tid]["name"]
    del tutors[tid]
    save_tutors()
    await call.message.edit_text(f"✅ Репетитор «{name}» удалён.")
    await state.clear()


# ==================== ПАНЕЛЬ ПРЕПОДАВАТЕЛЯ (с автонарезкой) ====================
@dp.message(F.text.in_(["👨‍🏫 Панель преподавателя"]))
async def tutor_panel(message: types.Message):
    user_id = message.from_user.id
    tutor_id = None
    for tid, t in tutors.items():
        if t.get("telegram_id") == user_id:
            tutor_id = tid
            break
    if not tutor_id:
        await message.answer("⛔ Вы не зарегистрированы как преподаватель.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Мои ученики", callback_data=f"tutor_students_{tutor_id}")],
        [InlineKeyboardButton(text="⚙️ Настроить расписание", callback_data=f"tutor_schedule_{tutor_id}")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Панель преподавателя:", reply_markup=keyboard)

# --- Мои ученики ---
@dp.callback_query(F.data.startswith("tutor_students_"))
async def show_students(call: CallbackQuery):
    tid = int(call.data.split("_")[-1])
    active = [b for b in bookings.values() if b["tutor_id"] == tid and b["status"] == "active"]
    if not active:
        await call.message.edit_text("У вас пока нет активных записей.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_tutor_panel_{tid}")]
        ]))
    else:
        text = "📋 Ваши ученики:\n\n"
        for b in active:
            text += f"📅 {b['date']} в {b['time_slot']}\n👤 {b['username']}\n📚 {b['subject']}\n\n"
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_tutor_panel_{tid}")]
        ]))

@dp.callback_query(F.data.startswith("back_to_tutor_panel_"))
async def back_to_tutor_panel(call: CallbackQuery):
    tid = int(call.data.split("_")[-1])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Мои ученики", callback_data=f"tutor_students_{tid}")],
        [InlineKeyboardButton(text="⚙️ Настроить расписание", callback_data=f"tutor_schedule_{tid}")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Панель преподавателя:", reply_markup=keyboard)

# --- Настройка расписания ---
@dp.callback_query(F.data.startswith("tutor_schedule_"))
async def schedule_main(call: CallbackQuery, state: FSMContext):
    tid = int(call.data.split("_")[-1])
    await state.update_data(tid=tid)
    sched = schedules.get(tid, {})
    text = "Ваше расписание:\n"
    for day in WEEKDAYS:
        slots = sched.get(day, [])
        text += f"{WEEKDAY_NAMES[day]}: {', '.join(slots) if slots else 'нет'}\n"
    buttons = [[InlineKeyboardButton(text=f"✏️ {WEEKDAY_NAMES[day]}", callback_data=f"sched_day_{day}")] for day in WEEKDAYS]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_tutor_panel_{tid}")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.choose_day)

@dp.callback_query(F.data.startswith("sched_day_"), StateFilter(TutorScheduleStates.choose_day))
async def edit_day(call: CallbackQuery, state: FSMContext):
    day = call.data.split("_")[2]
    await state.update_data(current_day=day)
    data = await state.get_data()
    tid = data["tid"]
    sched = schedules.get(tid, {})
    slots = sched.get(day, [])
    text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + "\n".join(f"• {s}" for s in slots) if slots else "Нет слотов."
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить слот", callback_data="add_slot")],
        [InlineKeyboardButton(text="📅 Заполнить промежуток", callback_data="add_range")],
        [InlineKeyboardButton(text="❌ Удалить слот", callback_data="del_slot")] if slots else [],
        [InlineKeyboardButton(text="🔙 К дням недели", callback_data="back_to_schedule")]
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.manage_day_slots)

@dp.callback_query(F.data == "back_to_schedule", StateFilter(TutorScheduleStates.manage_day_slots))
async def back_to_schedule(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tid = data["tid"]
    sched = schedules.get(tid, {})
    text = "Ваше расписание:\n"
    for day in WEEKDAYS:
        slots = sched.get(day, [])
        text += f"{WEEKDAY_NAMES[day]}: {', '.join(slots) if slots else 'нет'}\n"
    buttons = [[InlineKeyboardButton(text=f"✏️ {WEEKDAY_NAMES[day]}", callback_data=f"sched_day_{day}")] for day in WEEKDAYS]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_tutor_panel_{tid}")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.choose_day)

# Добавление одного слота
@dp.callback_query(F.data == "add_slot", StateFilter(TutorScheduleStates.manage_day_slots))
async def add_slot_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Введите временной слот в формате HH:MM-HH:MM, например 10:00-11:30:")
    await state.set_state(TutorScheduleStates.add_slot)

@dp.message(TutorScheduleStates.add_slot)
async def process_add_slot(message: Message, state: FSMContext):
    slot = message.text.strip()
    if len(slot.split("-")) != 2:
        await message.answer("Неверный формат. Используйте ЧЧ:ММ-ЧЧ:ММ")
        return
    data = await state.get_data()
    tid = data["tid"]
    day = data["current_day"]
    if tid not in schedules:
        schedules[tid] = {}
    if day not in schedules[tid]:
        schedules[tid][day] = []
    if slot in schedules[tid][day]:
        await message.answer("Такой слот уже существует.")
        return
    schedules[tid][day].append(slot)
    save_schedules()
    await message.answer("Слот добавлен.")
    # Возвращаемся к просмотру дня
    sched = schedules[tid]
    slots = sched.get(day, [])
    text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + "\n".join(f"• {s}" for s in slots) if slots else "Нет слотов."
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить слот", callback_data="add_slot")],
        [InlineKeyboardButton(text="📅 Заполнить промежуток", callback_data="add_range")],
        [InlineKeyboardButton(text="❌ Удалить слот", callback_data="del_slot")] if slots else [],
        [InlineKeyboardButton(text="🔙 К дням недели", callback_data="back_to_schedule")]
    ]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.manage_day_slots)

# Автонарезка промежутка на слоты по 1,5 часа
@dp.callback_query(F.data == "add_range", StateFilter(TutorScheduleStates.manage_day_slots))
async def add_range_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text(
        "Введите промежуток времени в формате ЧЧ:ММ-ЧЧ:ММ (например, 09:00-15:30).\n"
        "Бот автоматически разобьёт его на слоты по 1,5 часа."
    )
    await state.set_state(TutorScheduleStates.add_range)

@dp.message(TutorScheduleStates.add_range)
async def process_add_range(message: Message, state: FSMContext):
    text = message.text.strip()
    if "-" not in text:
        await message.answer("Неверный формат. Используйте ЧЧ:ММ-ЧЧ:ММ")
        return
    parts = text.split("-")
    if len(parts) != 2:
        await message.answer("Неверный формат.")
        return
    start_time, end_time = parts[0].strip(), parts[1].strip()
    slots = split_into_slots(start_time, end_time, duration_min=90)
    if not slots:
        await message.answer("Не удалось создать ни одного слота. Проверьте время.")
        return
    data = await state.get_data()
    tid = data["tid"]
    day = data["current_day"]
    if tid not in schedules:
        schedules[tid] = {}
    if day not in schedules[tid]:
        schedules[tid][day] = []
    added = 0
    for s in slots:
        if s not in schedules[tid][day]:
            schedules[tid][day].append(s)
            added += 1
    save_schedules()
    await message.answer(f"Добавлено {added} новых слотов (пропущены существующие).")
    # Возврат к просмотру дня
    sched = schedules[tid]
    slots = sched.get(day, [])
    text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + "\n".join(f"• {s}" for s in slots) if slots else "Нет слотов."
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить слот", callback_data="add_slot")],
        [InlineKeyboardButton(text="📅 Заполнить промежуток", callback_data="add_range")],
        [InlineKeyboardButton(text="❌ Удалить слот", callback_data="del_slot")] if slots else [],
        [InlineKeyboardButton(text="🔙 К дням недели", callback_data="back_to_schedule")]
    ]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.manage_day_slots)

# Удаление слота
@dp.callback_query(F.data == "del_slot", StateFilter(TutorScheduleStates.manage_day_slots))
async def del_slot_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    tid = data["tid"]
    day = data["current_day"]
    slots = schedules[tid].get(day, [])
    if not slots:
        await call.message.edit_text("Нет слотов для удаления.")
        return
    buttons = [[InlineKeyboardButton(text=s, callback_data=f"delslot_{s}")] for s in slots]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_schedule")])
    await call.message.edit_text("Выберите слот для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.delete_slot)

@dp.callback_query(F.data.startswith("delslot_"), StateFilter(TutorScheduleStates.delete_slot))
async def confirm_del_slot(call: CallbackQuery, state: FSMContext):
    slot = call.data.split("_", 1)[1]
    data = await state.get_data()
    tid = data["tid"]
    day = data["current_day"]
    if tid in schedules and day in schedules[tid] and slot in schedules[tid][day]:
        schedules[tid][day].remove(slot)
        save_schedules()
    await call.message.edit_text("Слот удалён.")
    slots = schedules[tid].get(day, [])
    text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + "\n".join(f"• {s}" for s in slots) if slots else "Нет слотов."
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить слот", callback_data="add_slot")],
        [InlineKeyboardButton(text="📅 Заполнить промежуток", callback_data="add_range")],
        [InlineKeyboardButton(text="❌ Удалить слот", callback_data="del_slot")] if slots else [],
        [InlineKeyboardButton(text="🔙 К дням недели", callback_data="back_to_schedule")]
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.manage_day_slots)

# ==================== ЗАПУСК ====================
async def main() -> None:
    load_tutors()
    load_schedules()
    load_bookings()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
