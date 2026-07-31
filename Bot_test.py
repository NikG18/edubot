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
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import CallbackQuery

# ==================== КОНСТАНТЫ ====================
ADMING_ID = 846400165
ADMINJ_ID = 5116346967
ADMIN_IDS = [ADMING_ID, ADMINJ_ID]

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Передайте его через export BOT_TOKEN=...")

DATA_FILE = "tutors.json"

# ==================== НАЧАЛЬНЫЕ ДАННЫЕ ====================
INITIAL_TUTORS = {
    "tutor_nikitaz": {
        "name": "Никита Тимурович",
        "description": "Приветствую, меня зовут Никита Тимурович Ганжа... (ваш текст)",
        "photo": None,
        "subjects": ["Химия", "Физика"],
        "prices": {"Химия": 2500, "Физика": 1500}
    },
    "tutor_juliaz": {
        "name": "Юлия Евгеньевна",
        "description": "Приветствую, меня зовут Юлия Евгеньевна Паймурзова...",
        "photo": None,
        "subjects": ["Химия"],
        "prices": {"Химия": 1500}
    },
    "tutor_nikitakz": {
        "name": "Никита Дмитриевич",
        "description": "Приветствую, меня зовут Никита Дмитриевич Колебаев...",
        "photo": None,
        "subjects": ["Химия", "Физика", "Математика", "Информатика"],
        "prices": {"Химия": 2500, "Физика": 2500, "Математика": 2500, "Информатика": 2500}
    }
}

# ==================== ЗАГРУЗКА / СОХРАНЕНИЕ ====================
def save_tutors(tutors):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(tutors, f, ensure_ascii=False, indent=2)
        print("✅ Данные сохранены в файл.")
    except Exception as e:
        print(f"❌ Ошибка сохранения: {e}")

def load_tutors():
    # Если файл есть – пробуем прочитать
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data and isinstance(data, dict) and len(data) > 0:
                # Нормализуем
                for key, tutor in data.items():
                    tutor.setdefault("subjects", [])
                    tutor.setdefault("prices", {})
                    if tutor["prices"] is None:
                        tutor["prices"] = {}
                    tutor.setdefault("photo", None)
                    tutor.setdefault("description", "")
                print(f"📂 Загружено {len(data)} репетиторов из файла.")
                return data
            else:
                print("⚠️ Файл пуст или повреждён. Создаю начальных репетиторов.")
        except Exception as e:
            print(f"❌ Ошибка чтения файла: {e}. Создаю начальных репетиторов.")
    else:
        print("📄 Файл не найден. Создаю начальных репетиторов.")

    # Если файла нет или он битый – создаём начальных и сразу сохраняем
    save_tutors(INITIAL_TUTORS)
    return INITIAL_TUTORS

tutors = load_tutors()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def get_all_subjects():
    subjects = set()
    for tutor in tutors.values():
        subjects.update(tutor.get("subjects", []))
    return sorted(subjects)

def get_tutors_by_subject(subject):
    result = []
    for key, val in tutors.items():
        if subject in val.get("subjects", []):
            result.append((key, val))
    return result

def get_main_menu(user_id: int):
    buttons = [
        [KeyboardButton(text="ℹ️ Информация о репетиторах")],
        [KeyboardButton(text="📚 Информация о занятиях")],
        [KeyboardButton(text="📝 Запись на занятие")],
        [KeyboardButton(text="💳 Оплата")],
        [KeyboardButton(text="📖 Учебные материалы")],
        [KeyboardButton(text="✉️ Связь с преподавателем")],
        [KeyboardButton(text="❓ Помощь")]
    ]
    if user_id in ADMIN_IDS:
        buttons.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ==================== FSM ====================
class BookingStates(StatesGroup):
    waiting_date_time = State()
    waiting_confirmation = State()

class AddTutorStates(StatesGroup):
    waiting_name = State()
    waiting_photo = State()
    waiting_description = State()
    waiting_subjects = State()
    waiting_prices = State()

class EditTutorStates(StatesGroup):
    waiting_name = State()
    waiting_photo = State()
    waiting_description = State()
    waiting_subjects = State()
    waiting_prices = State()

# ==================== ХЭНДЛЕРЫ ====================
@dp.message(Command("start"))
async def Start(message: Message) -> None:
    await message.answer(
        f"Привет, {html.bold(message.from_user.full_name)}! Я онлайн ассистент Никиты Тимуровича. Чем могу помочь?",
        reply_markup=get_main_menu(message.from_user.id)
    )

@dp.message(F.text.in_(["🔙 Назад"]))
async def main_menu_buttons(message: Message) -> None:
    await message.answer(
        f"{html.bold(message.from_user.full_name)}, Чем могу помочь?",
        reply_markup=get_main_menu(message.from_user.id)
    )

# ---------- Админ-панель ----------
@dp.message(F.text == "⚙️ Админ-панель")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет доступа к этой панели.", reply_markup=get_main_menu(message.from_user.id))
        return
    await message.answer("Переходим в админ-панель...", reply_markup=ReplyKeyboardRemove())
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить репетитора", callback_data="add_tutor")],
        [InlineKeyboardButton(text="📋 Список репетиторов", callback_data="list_tutors")]
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выберите действие:", reply_markup=keyboard)

# ---------- Добавление ----------
@dp.callback_query(F.data == "add_tutor")
async def start_add_tutor(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMING_ID:
        await call.answer("Нет прав.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text("Введите полное имя репетитора (например, Иван Петрович):")
    await state.set_state(AddTutorStates.waiting_name)
    await state.update_data(editing=False)

@dp.message(AddTutorStates.waiting_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("Теперь отправьте фото (или '-' чтобы пропустить):")
    await state.set_state(AddTutorStates.waiting_photo)

@dp.message(AddTutorStates.waiting_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(photo=file_id)
    await message.answer("Фото сохранено. Введите описание репетитора:")
    await state.set_state(AddTutorStates.waiting_description)

@dp.message(AddTutorStates.waiting_photo, F.text)
async def process_photo_text(message: types.Message, state: FSMContext):
    if message.text.strip() == "-":
        await state.update_data(photo=None)
        await message.answer("Фото пропущено. Введите описание:")
        await state.set_state(AddTutorStates.waiting_description)
    else:
        await message.answer("Отправьте фото или '-' для пропуска.")

@dp.message(AddTutorStates.waiting_description)
async def process_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await message.answer("Введите предметы через запятую (например: Химия, Физика):")
    await state.set_state(AddTutorStates.waiting_subjects)

@dp.message(AddTutorStates.waiting_subjects)
async def process_subjects(message: types.Message, state: FSMContext):
    subjects = [s.strip() for s in message.text.split(",") if s.strip()]
    if not subjects:
        await message.answer("Список предметов не может быть пустым. Попробуйте ещё раз:")
        return
    await state.update_data(subjects=subjects)
    await message.answer(
        f"Введите цены для предметов через запятую в том же порядке.\n"
        f"Предметы: {', '.join(subjects)}\n"
        "Пример: 2500, 1500"
    )
    await state.set_state(AddTutorStates.waiting_prices)

@dp.message(AddTutorStates.waiting_prices)
async def process_prices(message: types.Message, state: FSMContext):
    data = await state.get_data()
    subjects = data.get("subjects", [])
    try:
        prices = [int(p.strip()) for p in message.text.split(",") if p.strip()]
    except ValueError:
        await message.answer("Цены должны быть числами. Введите через запятую.")
        return
    if len(prices) != len(subjects):
        await message.answer(f"Количество цен ({len(prices)}) не совпадает с предметами ({len(subjects)}). Повторите.")
        return
    price_dict = dict(zip(subjects, prices))
    await state.update_data(prices=price_dict)

    # Сохраняем нового репетитора
    global tutors
    name = data.get("name")
    photo = data.get("photo")
    description = data.get("description")
    if not name or not description:
        await message.answer("Ошибка: имя или описание пустые. Начните заново.")
        await state.clear()
        return

    import time
    new_key = f"tutor_{int(time.time())}"
    tutors[new_key] = {
        "name": name,
        "description": description,
        "photo": photo,
        "subjects": subjects,
        "prices": price_dict
    }
    save_tutors(tutors)
    await message.answer(
        f"✅ Репетитор {name} успешно добавлен!\n"
        f"Предметы: {', '.join(subjects)}\n"
        f"Цены: {', '.join(f'{s}: {p} руб.' for s, p in price_dict.items())}",
        reply_markup=get_main_menu(message.from_user.id)
    )
    await state.clear()

# ---------- Список и просмотр (все остальные хэндлеры остаются такими же) ----------
# Я не буду дублировать их здесь, но они должны быть. 
# Вы можете взять их из предыдущего кода – они не менялись.

# ==================== ЗАПУСК ====================
async def main() -> None:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
