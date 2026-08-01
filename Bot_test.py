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

class ContactStates(StatesGroup):
    choosing_tutor = State()
    waiting_message = State()

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
    """Показать меню управления предметами репетитора."""
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
    try:
        await call.message.answer("Главное меню:", reply_markup=main_menu)
    except Exception:
        pass

# ==================== ИНФОРМАЦИЯ О РЕПЕТИТОРАХ (без изменений) ====================
# (Код остаётся как раньше, поэтому не дублирую для экономии места)

# ==================== ЗАПИСЬ НА ЗАНЯТИЕ (модифицированная отправка) ====================
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
    tutor_id = data.get("tutor_id")          # получаем id
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

    # Отправка админу
    try:
        await bot.send_message(chat_id=ADMING_ID, text=booking_message)
    except Exception as e:
        logging.error(f"Ошибка отправки админу: {e}")

    # Отправка преподавателю
    tutor = tutors.get(tutor_id)
    if tutor and tutor.get("telegram_id"):
        try:
            await bot.send_message(chat_id=tutor["telegram_id"], text=booking_message)
        except Exception as e:
            logging.error(f"Не удалось отправить преподавателю {tutor_name}: {e}")

    await call.message.edit_text("✅ Запись успешно подтверждена! Преподаватель свяжется с вами.")
    await call.message.answer("Вы записаны на занятие. Ожидайте подтверждения от преподавателя.", reply_markup=main_menu)
    await state.clear()

# Остальные хендлеры записи (change_datetime, cancel_booking) без изменений...

# ==================== СВЯЗЬ С ПРЕПОДАВАТЕЛЕМ (НОВЫЙ ФУНКЦИОНАЛ) ====================
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
        await state.clear()
        return
    await state.update_data(msg_tutor_id=tid, msg_tutor_name=tutor["name"])
    await call.message.edit_text(
        f"Вы пишете преподавателю {tutor['name']}.\n"
        "Введите ваше сообщение (текст):"
    )
    await state.set_state(ContactStates.waiting_message)

@dp.message(ContactStates.waiting_message)
async def send_message_to_tutor(message: Message, state: FSMContext, bot: Bot):
    user = message.from_user
    username = user.username or user.full_name
    user_id = user.id
    data = await state.get_data()
    tutor_id = data.get("msg_tutor_id")
    tutor_name = data.get("msg_tutor_name")
    text = message.text.strip()

    # Формируем сообщение для пересылки
    forward_msg = (
        f"📨 Сообщение от ученика\n"
        f"👤 {username} (ID: {user_id})\n"
        f"✉️ Преподавателю: {tutor_name}\n\n"
        f"💬 Текст:\n{text}\n\n"
        f"Ответить можно через @{username}" if user.username else f"ID: {user_id}"
    )

    # Отправляем админу
    try:
        await bot.send_message(chat_id=ADMING_ID, text=forward_msg)
    except Exception as e:
        logging.error(f"Ошибка отправки админу: {e}")

    # Отправляем преподавателю, если есть Telegram ID
    tutor = tutors.get(tutor_id)
    sent_to_tutor = False
    if tutor and tutor.get("telegram_id"):
        try:
            await bot.send_message(chat_id=tutor["telegram_id"], text=forward_msg)
            sent_to_tutor = True
        except Exception as e:
            logging.error(f"Не удалось отправить преподавателю {tutor_name}: {e}")

    if sent_to_tutor:
        await message.answer("✅ Ваше сообщение отправлено преподавателю.", reply_markup=main_menu)
    else:
        await message.answer(
            "⚠️ У преподавателя не указан Telegram ID, но сообщение передано администратору. С вами свяжутся.",
            reply_markup=main_menu
        )
    await state.clear()

# ==================== ОСТАЛЬНЫЕ РАЗДЕЛЫ (без изменений) ====================
# ... (весь остальной код из предыдущего ответа, включая админ-панель и пр.)

# ==================== ЗАПУСК ====================
async def main() -> None:
    load_tutors()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
