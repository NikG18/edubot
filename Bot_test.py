import asyncio
import logging
import sys
import json
import os
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
ADMINJ_ID = 5116346967
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Передайте его через export BOT_TOKEN=...")

dp = Dispatcher()

TUTORS_FILE = "tutors.json"

tutors = {}

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
                "telegram_id": None,   # новое поле
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

def save_tutors():
    with open(TUTORS_FILE, "w", encoding="utf-8") as f:
        json.dump(tutors, f, ensure_ascii=False, indent=2)

def get_next_tutor_id():
    if not tutors:
        return 1
    return max(tutors.keys()) + 1

# -------------------- FSM состояния --------------------
class BookingStates(StatesGroup):
    choosing_tutor = State()
    choosing_subject = State()
    waiting_date_time = State()
    waiting_confirmation = State()

class AdminStates(StatesGroup):
    waiting_name = State()
    waiting_photo = State()
    waiting_description = State()
    waiting_telegram_id = State()                 # новое состояние
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
        [KeyboardButton(text="👨‍🏫 Админ-панель")]
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
    """Показать меню управления предметами репетитора (используется и для Message, и для CallbackQuery)."""
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

# ==================== БАЗОВЫЕ ОБРАБОТЧИКИ ====================
@dp.message(Command("start"))
async def Start(message: Message) -> None:
    await message.answer(
        f"Привет, {html.bold(message.from_user.full_name)}! Я онлайн ассистент Никиты Тимуровича. Чем могу помочь?",
        reply_markup=main_menu
    )

@dp.message(F.text.in_(["🔙 Назад"]))
async def main_menu_buttons(message: Message) -> None:
    await message.answer(
        f"{html.bold(message.from_user.full_name)}, Чем могу помочь?",
        reply_markup=main_menu
    )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        pass
    # Если сообщение ещё существует (не удалено), отправляем новое
    try:
        await call.message.answer("Главное меню:", reply_markup=main_menu)
    except Exception:
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
    # Если сообщение с фото, его нельзя редактировать как текст — удаляем и отправляем новое
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

    # Описание и предметы (без telegram_id!)
    text = tutor["description"] + "\n\nПредметы и цены:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_tutors")]
    ])

    if tutor["photo"]:
        await call.message.delete()
        await call.bot.send_photo(
            chat_id=call.message.chat.id,
            photo=tutor["photo"],
            caption=text,
            reply_markup=keyboard
        )
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
    buttons = []
    for subj in sorted(subjects_set):
        buttons.append([InlineKeyboardButton(text=subj, callback_data=f"lesson_subject_{subj}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Какой предмет Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_lesson_subjects")
async def back_to_lesson_subjects(call: CallbackQuery):
    subjects_set = set()
    for t in tutors.values():
        subjects_set.update(t["subjects"].keys())
    buttons = []
    for subj in sorted(subjects_set):
        buttons.append([InlineKeyboardButton(text=subj, callback_data=f"lesson_subject_{subj}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await call.message.edit_text("Какой предмет Вас интересует?", reply_markup=keyboard)
    await call.answer()

@dp.callback_query(F.data.startswith("lesson_subject_"))
async def show_lesson_subject_info(call: CallbackQuery):
    subject = call.data.split("lesson_subject_", 1)[1]
    lines = [f"Предмет: {subject}", ""]
    found = False
    for t in tutors.values():
        if subject in t["subjects"]:
            lines.append(f"👨‍🏫 {t['name']} — {t['subjects'][subject]} руб.")
            found = True
    if not found:
        lines.append("Нет репетиторов по этому предмету.")
    text = "\n".join(lines)
    await call.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К списку предметов", callback_data="back_to_lesson_subjects")]
        ])
    )
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
    await call.message.edit_text(
        f"Вы выбрали предмет: {subject}.\n"
        "Теперь введите желаемую дату и время занятия в формате:\n"
        "ДД.ММ.ГГГГ ЧЧ:MM\n"
        "Например: 15.08.2026 14:30"
    )
    await state.set_state(BookingStates.waiting_date_time)

@dp.message(BookingStates.waiting_date_time)
async def process_date_time(message: types.Message, state: FSMContext):
    date_time_text = message.text.strip()
    if not date_time_text or len(date_time_text) < 10:
        await message.answer("Пожалуйста, введите дату и время в формате ДД.ММ.ГГГГ ЧЧ:MM")
        return
    await state.update_data(date_time=date_time_text)
    data = await state.get_data()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить запись", callback_data="confirm_booking")],
        [InlineKeyboardButton(text="✏️ Изменить дату/время", callback_data="change_datetime")],
        [InlineKeyboardButton(text="❌ Отменить запись", callback_data="cancel_booking")]
    ])
    await message.answer(
        f"Проверьте данные:\n"
        f"👨‍🏫 Репетитор: {data.get('tutor_name')}\n"
        f"📚 Предмет: {data.get('subject')}\n"
        f"📅 Дата и время: {date_time_text}\n\n"
        "Всё верно?",
        reply_markup=keyboard
    )
    await state.set_state(BookingStates.waiting_confirmation)

@dp.callback_query(F.data == "confirm_booking", StateFilter(BookingStates.waiting_confirmation))
async def confirm_booking(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    data = await state.get_data()
    tutor_name = data.get("tutor_name")
    subject = data.get("subject")
    date_time = data.get("date_time")
    user = call.from_user
    username = user.username or user.full_name
    user_id = user.id

    booking_message = (
        f"📝 Новая запись на занятие!\n\n"
        f"👤 Ученик: {username} (ID: {user_id})\n"
        f"👨‍🏫 Репетитор: {tutor_name}\n"
        f"📚 Предмет: {subject}\n"
        f"📅 Дата и время: {date_time}\n"
        f"📞 Связаться с учеником: @{username}" if username else f"ID: {user_id}"
    )

    try:
        await bot.send_message(chat_id=ADMING_ID, text=booking_message)
        await call.message.edit_text("✅ Запись успешно подтверждена! Преподаватель свяжется с вами.")
        await call.message.answer("Вы записаны на занятие. Ожидайте подтверждения от преподавателя.", reply_markup=main_menu)
    except Exception as e:
        await call.message.edit_text(f"❌ Произошла ошибка при отправке записи. Попробуйте позже.\nОшибка: {e}")
    finally:
        await state.clear()

@dp.callback_query(F.data == "change_datetime", StateFilter(BookingStates.waiting_confirmation))
async def change_datetime(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Введите новую дату и время в формате ДД.ММ.ГГГГ ЧЧ:MM")
    await state.set_state(BookingStates.waiting_date_time)

@dp.callback_query(F.data == "cancel_booking", StateFilter(BookingStates.waiting_confirmation))
async def cancel_booking(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Запись отменена. Возвращаемся в главное меню.")
    await state.clear()
    await call.message.answer("Главное меню:", reply_markup=main_menu)

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
    await call.message.edit_text(
        "📱 Сканируйте QR-код для оплаты в приложении вашего банка",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_pay")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "card")
async def card(call: CallbackQuery):
    await call.message.edit_text(
        "💳 Переходите по ссылке и следуйте дальнейшим инструкциям",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_pay")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "sbp")
async def sbp(call: CallbackQuery):
    await call.message.edit_text(
        "📲 Перевод выполняйте, указывая предмет и дату занятия, по номеру 89035370929 на Т-банк",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_pay")]
        ])
    )
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
    await call.answer()

@dp.callback_query(F.data == "book")
async def book(call: CallbackQuery):
    await call.message.edit_text(
        "📘 Учебники и таблицы",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧪 Химия", callback_data="bookh")],
            [InlineKeyboardButton(text="⚛️ Физика", callback_data="bookf")],
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_mat")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "vid")
async def vid(call: CallbackQuery):
    await call.message.edit_text(
        "🎥 Видеоматериалы (записи реакций и явлений)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧪 Химия", callback_data="videh")],
            [InlineKeyboardButton(text="⚛️ Физика", callback_data="videf")],
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_mat")]
        ])
    )
    await call.answer()

# ==================== СВЯЗЬ С ПРЕПОДАВАТЕЛЕМ ====================
@dp.message(F.text.in_(["✉️ Связь с преподавателем"]))
async def svyaz(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Отправить", callback_data="otprav")],
            [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
        ]
    )
    await message.answer(
        "Напишите, что вы хотите сообщить преподавателю, нажмите кнопку Отправить, после чего ожидайте ответа.",
        reply_markup=keyboard
    )

# ==================== ПОМОЩЬ ====================
@dp.message(F.text.in_(["❓ Помощь"]))
async def help(message: types.Message):
    await message.answer("Сообщаю Вам информацию о каждом разделе...", reply_markup=ReplyKeyboardRemove())
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
        ]
    )
    await message.answer(
        "В разделе ℹ️ Информация о репетиторах вы можете узнать об опыте и образовании каждого из преподавателей.\n"
        "В разделе 📚 Информация о занятиях вы найдёте прайслист каждого преподавателя.",
        reply_markup=keyboard
    )

# ==================== АДМИН-ПАНЕЛЬ ====================
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

# --- ДОБАВЛЕНИЕ РЕПЕТИТОРА (теперь с Telegram ID) ---
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
    await call.message.edit_text(f"✅ Репетитор «{data['name']}» успешно добавлен (ID {new_id}).")
    await state.clear()

# --- РЕДАКТИРОВАНИЕ РЕПЕТИТОРА (добавлен Telegram ID) ---
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
        [InlineKeyboardButton(text="Изменить Telegram ID", callback_data="edit_telegram_id")],  # новая кнопка
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
    # После успешного изменения показываем сообщение с кнопкой «Назад в меню»
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("✅ Изменения сохранены.", reply_markup=keyboard)
    await state.clear()

# --- УПРАВЛЕНИЕ ПРЕДМЕТАМИ (без изменений) ---
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

# ... (остальные обработчики предметов без изменений, они остаются такими же, как в предыдущей версии)

# ==================== ЗАПУСК ====================
async def main() -> None:
    load_tutors()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
