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
    waiting_reply = State()   # новое состояние для ответа

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
    delete_slot = State()

# -------------------- ГЛАВНОЕ МЕНЮ --------------------
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="ℹ️ Информация о репетиторах")],
        [KeyboardButton(text="📚 Информация о занятиях")],
        [KeyboardButton(text="📝 Запись на занятие")],
        [KeyboardButton(text="💳 Оплата")],
        [KeyboardButton(text="📖 Учебные материалы")],
        [KeyboardButton(text="✉️ Связь с преподавателем")],
        [KeyboardButton(text="❓ Помощь")],
        [KeyboardButton(text="👨‍🏫 Админ-панель")],
        [KeyboardButton(text="👨‍🏫 Панель преподавателя")]
    ],
    resize_keyboard=True
)

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

def get_available_dates(tutor_id: int, days_ahead=14) -> list:
    today = datetime.now()
    available = []
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        date_str = d.strftime("%d.%m.%Y")
        free = get_available_slots(tutor_id, date_str)
        if free:
            available.append(date_str)
    return available

# ==================== БАЗОВЫЕ ОБРАБОТЧИКИ ====================
@dp.message(Command("start"))
async def Start(message: Message) -> None:
    await message.answer(
        f"Привет, {html.bold(message.from_user.full_name)}! Я онлайн ассистент Никиты Тимуровича. Чем могу помочь?",
        reply_markup=main_menu
    )

@dp.message(F.text.in_(["🔙 Назад"]))
async def main_menu_buttons(message: Message) -> None:
    await message.answer("Главное меню:", reply_markup=main_menu)

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    try:
        await call.message.delete()
    except:
        pass
    try:
        await call.message.answer("Главное меню:", reply_markup=main_menu)
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
        await message.answer("Пока нет доступных предметов.", reply_markup=main_menu)
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

# ==================== ЗАПИСЬ НА ЗАНЯТИЕ ====================
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
    dates = get_available_dates(tid)
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
    await call.message.answer("Вы записаны на занятие. Ожидайте подтверждения от преподавателя.", reply_markup=main_menu)
    await state.clear()

@dp.callback_query(F.data == "cancel_booking", StateFilter(BookingStates.waiting_confirmation))
async def cancel_booking(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Запись отменена. Возвращаемся в главное меню.")
    await state.clear()
    await call.message.answer("Главное меню:", reply_markup=main_menu)

# ==================== ОПЛАТА / УЧЕБНЫЕ МАТЕРИАЛЫ (без изменений) ====================
# (Код разделов "Оплата" и "Учебные материалы" идентичен предыдущему)

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

    # Отправляем админу с кнопкой "Ответить"
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Ответить", callback_data=f"reply_{user.id}")]
    ])
    await bot.send_message(ADMING_ID, forward_msg, reply_markup=reply_markup)

    # Отправляем преподавателю, если указан Telegram ID
    tutor = tutors.get(tid)
    if tutor and tutor.get("telegram_id"):
        try:
            await bot.send_message(tutor["telegram_id"], forward_msg, reply_markup=reply_markup)
        except:
            pass

    await message.answer("✅ Сообщение отправлено. Ожидайте ответа.", reply_markup=main_menu)
    await state.clear()

# Обработчик кнопки "Ответить" (для админа и преподавателя)
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
        await message.answer("✅ Ответ отправлен ученику.", reply_markup=main_menu)
    except:
        await message.answer("⚠️ Не удалось отправить ответ (возможно, ученик заблокировал бота).", reply_markup=main_menu)
    await state.clear()

# ==================== АДМИН-ПАНЕЛЬ (с кнопкой "Назад") ====================
# ... (все хендлеры админки остаются прежними, кроме двух мест)

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

# ==================== ПАНЕЛЬ ПРЕПОДАВАТЕЛЯ (без изменений) ====================
# (весь код панели преподавателя остаётся как в предыдущей версии)

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
