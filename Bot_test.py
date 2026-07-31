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
from aiogram.types import Message, ReplyKeyboardRemove, FSInputFile
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import CallbackQuery


# ==================== КОНСТАНТЫ ====================
ADMING_ID = 846400165
ADMINJ_ID = 5116346967
ADMIN_IDS = [ADMING_ID]  
# 1. Токен бота (получите у @BotFather в Telegram)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Передайте его через export BOT_TOKEN=...")


DATA_FILE = "tutors.json"

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
dp = Dispatcher()
global tutors
# ==================== ЗАГРУЗКА / СОХРАНЕНИЕ ДАННЫХ ====================
def load_tutors():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, tutor in data.items():
            tutor.setdefault("subjects", [])
            tutor.setdefault("prices", {})
            if tutor["prices"] is None:
                tutor["prices"] = {}
            tutor.setdefault("photo", None)
            tutor.setdefault("description", "")
        return data
    else:
        # Начальные данные с тремя репетиторами (включая Никиту Дмитриевича)
        return {
            "tutor_nikitaz": {
                "name": "Никита Тимурович",
                "description": (
                    "Приветствую, меня зовут Никита Тимурович Ганжа кратко расскажу о себе. "
                    "Учусь в РНИМУ им. Пирогова на 4 курсе, в основном на отлично (на одной сессии была четверка). "
                    "Опыт преподавания имеется, вел курсы по химии в школе. Химией занимаюсь с 8 класса, "
                    "два раза был призером регионального этапа по химии, 99 баллов на ЕГЭ. "
                    "Хорошо разбираюсь в физике, биологии, математике и истории. "
                    "Работаю с учениками на фундаментальное понимание химии, а не «тут нужно просто выучить». "
                    "Объясняю на примерах из жизни, из человеческого организма и природы. "
                    "Если вам кажется, что вы совсем не знаете химию, то я изменю это уже к 4 занятию. "
                    "Первое пробное занятие 30 минут, бесплатно."
                ),
                "photo": None,
                "subjects": ["Химия", "Физика"],
                "prices": {"Химия": 2500, "Физика": 1500}
            },
            "tutor_juliaz": {
                "name": "Юлия Евгеньевна",
                "description": "Приветствую, меня зовут Юлия Евгеньевна Паймурзова...\n\nХотите записаться на пробное занятие?",
                "photo": None,
                "subjects": ["Химия"],
                "prices": {"Химия": 1500}
            },
            "tutor_nikitakz": {
                "name": "Никита Дмитриевич",
                "description": "Приветствую, меня зовут Никита Дмитриевич Колебаев...\n\nХотите записаться на пробное занятие?",
                "photo": None,
                "subjects": ["Химия", "Физика", "Математика", "Информатика"],
                "prices": {"Химия": 2500, "Физика": 2500, "Математика": 2500, "Информатика": 2500}
            }
        }

def save_tutors(tutors):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(tutors, f, ensure_ascii=False, indent=2)

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

# ==================== FSM СОСТОЯНИЯ ====================
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

# ==================== АДМИН-ПАНЕЛЬ ====================
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

# ---------- Добавление репетитора ----------
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
    data = await state.get_data()
    if data.get("editing"):
        if message.text.strip() in ("", "-"):
            await state.update_data(name_unchanged=True)
        else:
            await state.update_data(name=message.text.strip())
    else:
        await state.update_data(name=message.text.strip())
    await message.answer("Теперь отправьте фото репетитора (просто пришлите фото). Если хотите пропустить, отправьте '-'")
    await state.set_state(AddTutorStates.waiting_photo)

@dp.message(AddTutorStates.waiting_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(photo=file_id)
    await message.answer("Фото сохранено. Теперь введите описание репетитора (можно несколько предложений):")
    await state.set_state(AddTutorStates.waiting_description)

@dp.message(AddTutorStates.waiting_photo, F.text)
async def process_photo_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if message.text.strip() == "-":
        if data.get("editing"):
            await state.update_data(photo_unchanged=True)
        else:
            await state.update_data(photo=None)
        await message.answer("Фото не будет добавлено. Введите описание репетитора:")
        await state.set_state(AddTutorStates.waiting_description)
    else:
        await message.answer("Пожалуйста, отправьте именно фото (изображение) или '-' для пропуска.")

@dp.message(AddTutorStates.waiting_description)
async def process_description(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if data.get("editing") and message.text.strip() in ("", "-"):
        await state.update_data(description_unchanged=True)
    else:
        await state.update_data(description=message.text.strip())
    await message.answer(
        "Введите список предметов, которые ведёт репетитор, через запятую.\n"
        "Пример: Химия, Физика, Математика"
    )
    await state.set_state(AddTutorStates.waiting_subjects)

@dp.message(AddTutorStates.waiting_subjects)
async def process_subjects(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if data.get("editing") and message.text.strip() in ("", "-"):
        await state.update_data(subjects_unchanged=True)
    else:
        subjects = [s.strip() for s in message.text.split(",") if s.strip()]
        if not subjects:
            await message.answer("Список не может быть пустым. Введите предметы через запятую.")
            return
        await state.update_data(subjects=subjects)

    # Запрашиваем цены
    data = await state.get_data()
    if data.get("editing") and message.text.strip() in ("", "-"):
        edit_key = data.get("edit_key")
        old_tutor = tutors.get(edit_key)
        old_subjects = old_tutor.get("subjects", []) if old_tutor else []
        old_prices = old_tutor.get("prices", {}) if old_tutor else {}
        if old_prices is None:
            old_prices = {}
        await message.answer(
            f"Введите цены для предметов в том же порядке, через запятую.\n"
            f"Сейчас предметы: {', '.join(old_subjects) if old_subjects else 'нет'}\n"
            f"Текущие цены: {', '.join(f'{s}: {old_prices.get(s, "?")}' for s in old_subjects) if old_subjects else 'нет цен'}\n"
            "или отправьте '-' чтобы оставить текущие цены."
        )
    else:
        subjects = data.get("subjects", [])
        await message.answer(
            f"Введите цены для каждого предмета через запятую в том же порядке.\n"
            f"Предметы: {', '.join(subjects) if subjects else 'нет'}\n"
            "Пример: 2500, 1500, 2000"
        )
    await state.set_state(AddTutorStates.waiting_prices)

@dp.message(AddTutorStates.waiting_prices)
async def process_prices(message: types.Message, state: FSMContext):
    data = await state.get_data()
    editing = data.get("editing", False)

    if not editing:
        subjects = data.get("subjects", [])
        if not subjects:
            await message.answer("Вы не ввели предметы. Пожалуйста, введите список предметов через запятую.")
            await state.set_state(AddTutorStates.waiting_subjects)
            return

    if editing and message.text.strip() in ("", "-"):
        await state.update_data(prices_unchanged=True)
    else:
        try:
            prices = [int(p.strip()) for p in message.text.split(",") if p.strip()]
        except ValueError:
            await message.answer("Цены должны быть числами. Введите через запятую, например: 2500, 1500")
            return

        if editing:
            if data.get("subjects_unchanged"):
                edit_key = data.get("edit_key")
                old_tutor = tutors.get(edit_key)
                subjects = old_tutor.get("subjects", []) if old_tutor else []
            else:
                subjects = data.get("subjects", [])
        else:
            subjects = data.get("subjects", [])

        if not subjects:
            await message.answer("Список предметов пуст. Сначала укажите предметы.")
            if not editing:
                await state.set_state(AddTutorStates.waiting_subjects)
            else:
                await state.set_state(EditTutorStates.waiting_subjects)
            return

        if len(prices) != len(subjects):
            await message.answer(f"Количество цен ({len(prices)}) не совпадает с количеством предметов ({len(subjects)}). Повторите ввод.")
            return
        price_dict = dict(zip(subjects, prices))
        await state.update_data(prices=price_dict)

    
    if editing:
        edit_key = data.get("edit_key")
        old_tutor = tutors.get(edit_key)
        if not old_tutor:
            await message.answer("Ошибка: репетитор не найден.")
            await state.clear()
            return
        new_name = data.get("name") if not data.get("name_unchanged") else old_tutor.get("name", "")
        new_photo = data.get("photo") if not data.get("photo_unchanged") else old_tutor.get("photo")
        new_description = data.get("description") if not data.get("description_unchanged") else old_tutor.get("description", "")
        new_subjects = data.get("subjects") if not data.get("subjects_unchanged") else old_tutor.get("subjects", [])
        new_prices = data.get("prices") if not data.get("prices_unchanged") else old_tutor.get("prices", {})
        if new_prices is None:
            new_prices = {}
        tutors[edit_key] = {
            "name": new_name,
            "description": new_description,
            "photo": new_photo,
            "subjects": new_subjects,
            "prices": new_prices
        }
        save_tutors(tutors)
        await message.answer(
            f"✅ Репетитор {new_name} успешно обновлён!\n"
            f"Предметы: {', '.join(new_subjects) if new_subjects else 'нет'}\n"
            f"Цены: {', '.join(f'{s}: {p} руб.' for s, p in new_prices.items()) if new_prices else 'не указаны'}",
            reply_markup=get_main_menu(message.from_user.id)
        )
    else:
        name = data.get("name")
        photo = data.get("photo")
        description = data.get("description")
        subjects = data.get("subjects", [])
        prices = data.get("prices", {})
        if not name or not description or not subjects or not prices:
            await message.answer("Ошибка: не все данные заполнены. Попробуйте заново.")
            await state.clear()
            return
        import time
        new_key = f"tutor_{int(time.time())}"
        tutors[new_key] = {
            "name": name,
            "description": description,
            "photo": photo,
            "subjects": subjects,
            "prices": prices
        }
        save_tutors(tutors)
        await message.answer(
            f"✅ Репетитор {name} успешно добавлен!\n"
            f"Предметы: {', '.join(subjects)}\n"
            f"Цены: {', '.join(f'{s}: {p} руб.' for s, p in prices.items())}",
            reply_markup=get_main_menu(message.from_user.id)
        )
    await state.clear()

# ---------- Просмотр списка репетиторов ----------
@dp.callback_query(F.data == "list_tutors")
async def list_tutors_admin(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Нет прав.", show_alert=True)
        return
    await call.answer()
    if not tutors:
        await call.message.edit_text("Список репетиторов пуст.")
        return
    text = "📋 Список репетиторов:\n\n"
    for key, tutor in tutors.items():
        subjects = tutor.get("subjects", [])
        text += f"• {tutor.get('name', 'Без имени')} (предметы: {', '.join(subjects) if subjects else 'нет'})\n"
    buttons = []
    for key, tutor in tutors.items():
        row = [InlineKeyboardButton(text=f"👤 {tutor.get('name', 'Без имени')}", callback_data=f"view_tutor_{key}")]
        if call.from_user.id == ADMING_ID:
            row.append(InlineKeyboardButton(text="✏️", callback_data=f"edit_tutor_{key}"))
            row.append(InlineKeyboardButton(text="🗑", callback_data=f"delete_tutor_{key}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="back_to_admin")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await call.message.edit_text(text, reply_markup=keyboard)

# ---------- Просмотр конкретного репетитора ----------
@dp.callback_query(F.data.startswith("view_tutor_"))
async def view_tutor_admin(call: CallbackQuery):
    tutor_key = call.data.replace("view_tutor_", "")
    tutor = tutors.get(tutor_key)
    if not tutor:
        await call.answer("Репетитор не найден", show_alert=True)
        return
    await call.answer()
    subjects = tutor.get("subjects", [])
    prices = tutor.get("prices", {})
    if prices is None:
        prices = {}
    text = f"👨‍🏫 {tutor.get('name', 'Без имени')}\n\n{tutor.get('description', '')}\n\nПредметы: {', '.join(subjects) if subjects else 'нет'}\nЦены: {', '.join(f'{s}: {p} руб.' for s, p in prices.items()) if prices else 'не указаны'}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="list_tutors")]
    ])
    if tutor.get("photo"):
        await call.message.delete()
        await call.message.answer_photo(photo=tutor["photo"], caption=text, reply_markup=keyboard)
    else:
        await call.message.edit_text(text, reply_markup=keyboard)

# ---------- Удаление репетитора ----------
@dp.callback_query(F.data.startswith("delete_tutor_"))
async def delete_tutor_prompt(call: CallbackQuery):
    if call.from_user.id != ADMING_ID:
        await call.answer("Нет прав.", show_alert=True)
        return
    tutor_key = call.data.replace("delete_tutor_", "")
    tutor = tutors.get(tutor_key)
    if not tutor:
        await call.answer("Репетитор не найден", show_alert=True)
        return
    await call.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{tutor_key}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="list_tutors")]
    ])
    await call.message.edit_text(
        f"Вы уверены, что хотите удалить репетитора {tutor.get('name', 'Без имени')}?",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("confirm_delete_"))
async def confirm_delete_tutor(call: CallbackQuery):
    if call.from_user.id != ADMING_ID:
        await call.answer("Нет прав.", show_alert=True)
        return
    tutor_key = call.data.replace("confirm_delete_", "")
    if tutor_key not in tutors:
        await call.answer("Репетитор уже удалён", show_alert=True)
        return
    del tutors[tutor_key]
    save_tutors(tutors)
    await call.answer("Репетитор удалён.", show_alert=True)
    await list_tutors_admin(call)

# ---------- Редактирование репетитора ----------
@dp.callback_query(F.data.startswith("edit_tutor_"))
async def start_edit_tutor(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMING_ID:
        await call.answer("Нет прав.", show_alert=True)
        return
    tutor_key = call.data.replace("edit_tutor_", "")
    tutor = tutors.get(tutor_key)
    if not tutor:
        await call.answer("Репетитор не найден", show_alert=True)
        return
    await call.answer()
    await state.update_data(editing=True, edit_key=tutor_key)
    await state.update_data(name_unchanged=False, photo_unchanged=False, description_unchanged=False, subjects_unchanged=False, prices_unchanged=False)
    await call.message.edit_text(
        f"Редактирование репетитора: {tutor.get('name', 'Без имени')}\n"
        "Введите новое имя (или отправьте '-' чтобы оставить без изменений):"
    )
    await state.set_state(EditTutorStates.waiting_name)

@dp.message(EditTutorStates.waiting_name)
async def edit_process_name(message: types.Message, state: FSMContext):
    if message.text.strip() in ("", "-"):
        await state.update_data(name_unchanged=True)
    else:
        await state.update_data(name=message.text.strip())
    await message.answer(
        "Теперь отправьте новое фото (или '-' чтобы оставить текущее)."
    )
    await state.set_state(EditTutorStates.waiting_photo)

@dp.message(EditTutorStates.waiting_photo, F.photo)
async def edit_process_photo(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(photo=file_id)
    await message.answer("Фото обновлено. Теперь введите новое описание (или '-' чтобы оставить):")
    await state.set_state(EditTutorStates.waiting_description)

@dp.message(EditTutorStates.waiting_photo, F.text)
async def edit_process_photo_text(message: types.Message, state: FSMContext):
    if message.text.strip() == "-":
        await state.update_data(photo_unchanged=True)
        await message.answer("Фото остаётся прежним. Введите новое описание (или '-' чтобы оставить):")
    else:
        await message.answer("Отправьте фото или '-' для пропуска.")
        return
    await state.set_state(EditTutorStates.waiting_description)

@dp.message(EditTutorStates.waiting_description)
async def edit_process_description(message: types.Message, state: FSMContext):
    if message.text.strip() in ("", "-"):
        await state.update_data(description_unchanged=True)
    else:
        await state.update_data(description=message.text.strip())
    data = await state.get_data()
    edit_key = data.get("edit_key")
    old_tutor = tutors.get(edit_key)
    old_subjects = old_tutor.get("subjects", []) if old_tutor else []
    await message.answer(
        f"Введите новые предметы через запятую (сейчас: {', '.join(old_subjects) if old_subjects else 'нет'})\n"
        "или отправьте '-' чтобы оставить без изменений."
    )
    await state.set_state(EditTutorStates.waiting_subjects)

@dp.message(EditTutorStates.waiting_subjects)
async def edit_process_subjects(message: types.Message, state: FSMContext):
    data = await state.get_data()
    edit_key = data.get("edit_key")
    old_tutor = tutors.get(edit_key)
    if not old_tutor:
        await message.answer("Ошибка: репетитор не найден.")
        await state.clear()
        return

    if message.text.strip() in ("", "-"):
        await state.update_data(subjects_unchanged=True)
    else:
        subjects = [s.strip() for s in message.text.split(",") if s.strip()]
        if not subjects:
            await message.answer("Список не может быть пустым. Введите предметы через запятую или '-' для пропуска.")
            return
        await state.update_data(subjects=subjects)

    old_subjects = old_tutor.get("subjects", [])
    old_prices = old_tutor.get("prices", {})
    if old_prices is None:
        old_prices = {}

    if data.get("subjects_unchanged"):
        subjects_list = old_subjects
    else:
        subjects_list = data.get("subjects", old_subjects)

    prices_str = ', '.join(f'{s}: {old_prices.get(s, "?")}' for s in subjects_list) if subjects_list else "нет цен"
    await message.answer(
        f"Введите цены для предметов в том же порядке, через запятую.\n"
        f"Сейчас предметы: {', '.join(subjects_list) if subjects_list else 'нет'}\n"
        f"Текущие цены: {prices_str}\n"
        "или отправьте '-' чтобы оставить текущие цены."
    )
    await state.set_state(EditTutorStates.waiting_prices)

@dp.message(EditTutorStates.waiting_prices)
async def edit_process_prices(message: types.Message, state: FSMContext):
    data = await state.get_data()
    edit_key = data.get("edit_key")
    old_tutor = tutors.get(edit_key)
    if not old_tutor:
        await message.answer("Ошибка: репетитор не найден.")
        await state.clear()
        return

    if message.text.strip() in ("", "-"):
        await state.update_data(prices_unchanged=True)
    else:
        try:
            prices = [int(p.strip()) for p in message.text.split(",") if p.strip()]
        except ValueError:
            await message.answer("Цены должны быть числами. Введите через запятую, например: 2500, 1500")
            return
        if data.get("subjects_unchanged"):
            subjects = old_tutor.get("subjects", [])
        else:
            subjects = data.get("subjects", [])
        if not subjects:
            await message.answer("Список предметов пуст. Сначала укажите предметы.")
            await state.set_state(EditTutorStates.waiting_subjects)
            return
        if len(prices) != len(subjects):
            await message.answer(f"Количество цен ({len(prices)}) не совпадает с количеством предметов ({len(subjects)}). Повторите ввод.")
            return
        price_dict = dict(zip(subjects, prices))
        await state.update_data(prices=price_dict)

    new_name = data.get("name") if not data.get("name_unchanged") else old_tutor.get("name", "")
    new_photo = data.get("photo") if not data.get("photo_unchanged") else old_tutor.get("photo")
    new_description = data.get("description") if not data.get("description_unchanged") else old_tutor.get("description", "")
    new_subjects = data.get("subjects") if not data.get("subjects_unchanged") else old_tutor.get("subjects", [])
    new_prices = data.get("prices") if not data.get("prices_unchanged") else old_tutor.get("prices", {})
    if new_prices is None:
        new_prices = {}

    tutors[edit_key] = {
        "name": new_name,
        "description": new_description,
        "photo": new_photo,
        "subjects": new_subjects,
        "prices": new_prices
    }
    save_tutors(tutors)
    await message.answer(
        f"✅ Репетитор {new_name} успешно обновлён!\n"
        f"Предметы: {', '.join(new_subjects) if new_subjects else 'нет'}\n"
        f"Цены: {', '.join(f'{s}: {p} руб.' for s, p in new_prices.items()) if new_prices else 'не указаны'}",
        reply_markup=get_main_menu(message.from_user.id)
    )
    await state.clear()

# ---------- Назад в админ-панель ----------
@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin(call: CallbackQuery):
    await call.answer()
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить репетитора", callback_data="add_tutor")],
        [InlineKeyboardButton(text="📋 Список репетиторов", callback_data="list_tutors")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await call.message.edit_text("Выберите действие:", reply_markup=keyboard)

# ==================== ОСТАЛЬНЫЕ РАЗДЕЛЫ (для пользователей) ====================
@dp.message(F.text.in_(["ℹ️ Информация о репетиторах"]))
async def repet(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    buttons = []
    for key, tutor in tutors.items():
        buttons.append([InlineKeyboardButton(text=f"👨‍🏫 {tutor.get('name', 'Без имени')}", callback_data=f"tutor_{key}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_tutors")
async def back_to_tutors(call: CallbackQuery):
    buttons = []
    for key, tutor in tutors.items():
        buttons.append([InlineKeyboardButton(text=f"👨‍🏫 {tutor.get('name', 'Без имени')}", callback_data=f"tutor_{key}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await call.message.edit_text("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    await call.answer()

@dp.callback_query(F.data.startswith("tutor_"))
async def show_tutor_info(call: CallbackQuery):
    tutor_key = call.data.replace("tutor_", "")
    tutor = tutors.get(tutor_key)
    if not tutor:
        await call.message.edit_text("Репетитор не найден.")
        await call.answer()
        return
    await call.answer()
    text = f"👨‍🏫 {tutor.get('name', 'Без имени')}\n\n{tutor.get('description', '')}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_tutors")]
    ])
    if tutor.get("photo"):
        await call.message.delete()
        await call.message.answer_photo(photo=tutor["photo"], caption=text, reply_markup=keyboard)
    else:
        await call.message.edit_text(text, reply_markup=keyboard)

@dp.message(F.text.in_(["📚 Информация о занятиях"]))
async def lesson_info(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    subjects = get_all_subjects()
    buttons = []
    for subj in subjects:
        buttons.append([InlineKeyboardButton(text=f"📚 {subj}", callback_data=f"subject_{subj}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Какой предмет Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_sj")
async def back_to_sj(call: CallbackQuery):
    subjects = get_all_subjects()
    buttons = []
    for subj in subjects:
        buttons.append([InlineKeyboardButton(text=f"📚 {subj}", callback_data=f"subject_{subj}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await call.message.edit_text("Какой предмет Вас интересует?", reply_markup=keyboard)
    await call.answer()

@dp.callback_query(F.data.startswith("subject_"))
async def show_subject_tutors(call: CallbackQuery):
    subject = call.data.replace("subject_", "")
    tutors_list = get_tutors_by_subject(subject)
    if not tutors_list:
        await call.message.edit_text("По этому предмету пока нет репетиторов.")
        await call.answer()
        return
    text = f"🧪 Предмет: {subject}\n\nПреподают:\n"
    for key, tutor in tutors_list:
        price = tutor.get("prices", {}).get(subject, "не указана")
        text += f"• {tutor.get('name', 'Без имени')} — {price} руб./час\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Показать цены", callback_data=f"price_{subject}")],
        [InlineKeyboardButton(text="🔙 Назад к предметам", callback_data="back_to_sj")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard)
    await call.answer()

@dp.callback_query(F.data.startswith("price_"))
async def show_prices(call: CallbackQuery):
    subject = call.data.replace("price_", "")
    tutors_list = get_tutors_by_subject(subject)
    if not tutors_list:
        await call.message.edit_text("Нет репетиторов по этому предмету.")
        await call.answer()
        return
    text = f"💰 Цены на занятия по предмету «{subject}»:\n\n"
    for key, tutor in tutors_list:
        price = tutor.get("prices", {}).get(subject, "не указана")
        text += f"• {tutor.get('name', 'Без имени')} — {price} руб./час\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к предмету", callback_data=f"subject_{subject}")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard)
    await call.answer()

@dp.message(F.text.in_(["📝 Запись на занятие"]))
async def zapis(message: types.Message, state: FSMContext):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    buttons = []
    for key, tutor in tutors.items():
        buttons.append([InlineKeyboardButton(text=f"👨‍🏫 {tutor.get('name', 'Без имени')}", callback_data=f"booking_tutor_{key}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    await state.clear()

@dp.callback_query(F.data.startswith("booking_tutor_"))
async def booking_choose_tutor(call: CallbackQuery, state: FSMContext):
    tutor_key = call.data.replace("booking_tutor_", "")
    tutor = tutors.get(tutor_key)
    if not tutor:
        await call.answer("Репетитор не найден", show_alert=True)
        return
    await call.answer()
    await state.update_data(tutor=tutor.get("name", "Без имени"))
    buttons = []
    for subj in tutor.get("subjects", []):
        buttons.append([InlineKeyboardButton(text=f"📚 {subj}", callback_data=f"booking_subject_{subj}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к репетиторам", callback_data="back_to_booking_tutors")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await call.message.edit_text(f"Вы выбрали {tutor.get('name', 'Без имени')}. Теперь выберите предмет:", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_booking_tutors")
async def back_to_booking_tutors(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    buttons = []
    for key, tutor in tutors.items():
        buttons.append([InlineKeyboardButton(text=f"👨‍🏫 {tutor.get('name', 'Без имени')}", callback_data=f"booking_tutor_{key}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await call.message.edit_text("Кто из репетиторов Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("booking_subject_"))
async def booking_choose_subject(call: CallbackQuery, state: FSMContext):
    subject = call.data.replace("booking_subject_", "")
    data = await state.get_data()
    tutor_name = data.get("tutor")
    if not tutor_name:
        await call.answer("Ошибка, начните заново.", show_alert=True)
        return
    await state.update_data(subject=subject)
    await call.message.edit_text(
        f"Вы выбрали предмет: {subject}.\n"
        "Теперь введите желаемую дату и время занятия в формате:\n"
        "ДД.ММ.ГГГГ ЧЧ:MM\n"
        "Например: 15.08.2026 14:30"
    )
    await state.set_state(BookingStates.waiting_date_time)
    await call.answer()

@dp.message(BookingStates.waiting_date_time)
async def process_date_time(message: types.Message, state: FSMContext):
    date_time_text = message.text.strip()
    if not date_time_text or len(date_time_text) < 10:
        await message.answer("Пожалуйста, введите дату и время в формате ДД.ММ.ГГГГ ЧЧ:MM")
        return
    await state.update_data(date_time=date_time_text)
    data = await state.get_data()
    tutor = data.get("tutor")
    subject = data.get("subject")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить запись", callback_data="confirm_booking")],
        [InlineKeyboardButton(text="✏️ Изменить дату/время", callback_data="change_datetime")],
        [InlineKeyboardButton(text="❌ Отменить запись", callback_data="cancel_booking")]
    ])
    await message.answer(
        f"Проверьте данные:\n"
        f"👨‍🏫 Репетитор: {tutor}\n"
        f"📚 Предмет: {subject}\n"
        f"📅 Дата и время: {date_time_text}\n\n"
        "Всё верно?",
        reply_markup=keyboard
    )
    await state.set_state(BookingStates.waiting_confirmation)

@dp.callback_query(F.data == "confirm_booking", StateFilter(BookingStates.waiting_confirmation))
async def confirm_booking(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    data = await state.get_data()
    tutor = data.get("tutor")
    subject = data.get("subject")
    date_time = data.get("date_time")
    user = call.from_user
    username = user.username or user.full_name
    user_id = user.id

    booking_message = (
        f"📝 Новая запись на занятие!\n\n"
        f"👤 Ученик: {username} (ID: {user_id})\n"
        f"👨‍🏫 Репетитор: {tutor}\n"
        f"📚 Предмет: {subject}\n"
        f"📅 Дата и время: {date_time}\n"
        f"📞 Связаться с учеником: @{username}" if username else f"ID: {user_id}"
    )

    try:
        await bot.send_message(chat_id=ADMING_ID, text=booking_message)
        await call.message.edit_text("✅ Запись успешно подтверждена! Преподаватель свяжется с вами.")
        await call.message.answer("Вы записаны на занятие. Ожидайте подтверждения от преподавателя.", reply_markup=get_main_menu(call.from_user.id))
    except Exception as e:
        await call.message.edit_text(f"❌ Произошла ошибка при отправке записи. Попробуйте позже.\nОшибка: {e}")
    finally:
        await state.clear()

@dp.callback_query(F.data == "change_datetime", StateFilter(BookingStates.waiting_confirmation))
async def change_datetime(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text(
        "Введите новую дату и время в формате ДД.ММ.ГГГГ ЧЧ:MM"
    )
    await state.set_state(BookingStates.waiting_date_time)

@dp.callback_query(F.data == "cancel_booking", StateFilter(BookingStates.waiting_confirmation))
async def cancel_booking(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_text("Запись отменена. Возвращаемся в главное меню.")
    await state.clear()
    await call.message.answer("Главное меню:", reply_markup=get_main_menu(call.from_user.id))

# -------------------- ОПЛАТА --------------------
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

# -------------------- УЧЕБНЫЕ МАТЕРИАЛЫ --------------------
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

# -------------------- СВЯЗЬ С ПРЕПОДАВАТЕЛЕМ --------------------
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

# -------------------- ПОМОЩЬ --------------------
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

# ==================== ГЛОБАЛЬНАЯ КНОПКА НАЗАД В МЕНЮ ====================
@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await call.message.delete()
    await call.message.answer("Главное меню:", reply_markup=get_main_menu(call.from_user.id))

# ==================== ЗАПУСК ====================
async def main() -> None:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
