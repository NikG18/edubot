import asyncio
import logging
import sys
import re
import os
import aiosqlite
from datetime import datetime, timedelta, timezone
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram import Bot, Dispatcher, html, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, StateFilter
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, ReplyKeyboardRemove, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery)
from database import (
    init_db, migrate_database,
    get_all_tutors, add_tutor, update_tutor, delete_tutor,
    add_subject, update_subject, delete_subject,
    get_schedule, add_schedule_slot, delete_schedule_slot,
    get_all_bookings, add_booking, update_booking, delete_booking,
    get_tutor_by_telegram_id, get_student_subscriptions, get_tutor_financials, get_all_tutors_stats,
    get_students_stats, get_all_tutors_stats_by_month, get_students_stats_by_month,
    block_day, unblock_day, is_day_blocked
)
from aiogram.exceptions import TelegramBadRequest


async def safe_answer(call: CallbackQuery, text: str = None, show_alert: bool = False):
    """
    Безопасно отвечает на callback, игнорируя ошибку 'query is too old'
    """
    try:
        await call.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" in str(e):
            logging.warning(f"Callback query too old (ID: {call.id})")
        else:
            logging.error(f"Unexpected error in safe_answer: {e}")


def parse_booking_time(booking: dict) -> datetime:
    """
    Безопасно парсит дату и время начала занятия из бронирования.
    Заменяет точки на двоеточие в строке времени.
    """
    date_str = booking["date"]
    time_part = booking["time_slot"].split("-")[0].replace(".", ":")
    return datetime.strptime(f"{date_str} {time_part}", "%d.%m.%Y %H:%M")


ADMING_ID = int(os.environ.get("ADMING_ID"))
RECORDS_CHANNEL_ID = os.environ.get("RECORDS_CHANNEL_ID")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Передайте его через export BOT_TOKEN=...")

dp = Dispatcher()

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_NAMES = {
    "monday": "Пн", "tuesday": "Вт", "wednesday": "Ср",
    "thursday": "Чт", "friday": "Пт", "saturday": "Сб", "sunday": "Вс"
}

TUTOR_INFO_TEXT = (
    "👨‍🏫 **Информация для преподавателей**\n\n"
    "📍 **Где проходят занятия?**\n"
    "Занятия проводятся онлайн на платформе **Zoom** или **яндекс телемост**. "
    #"поэтому нет ограничений по времени и количеству участников. Ссылка на занятие генерируется автоматически.\n\n"
    "💰 **Как проходит оплата?**\n"
    "Ученики оплачивают занятия напрямую платформе. Мы удерживаем комиссию и перечисляем вам "
    "вознаграждение за вычетом комиссии **два раза в месяц** (12-го и 27-го числа).\n\n"
    "📈 **Прогрессивная шкала комиссии:**\n"
    "• 25% — 1-20 занятий в месяц\n"
    "• 20% — 21-40 занятий в месяц (доступно после 2 месяцев работы)\n"
    "• 15% — более 40 занятий в месяц (доступно после 4 месяцев работы)\n\n"
    "📝 **Ваши задачи:**\n"
    "— Подготовка и проведение занятий.\n"
    "— Обратная связь ученикам.(если возникают вопросы по домашнему заданию или по предмету в принципе\n"
    "— Выставление временных слотов удобных для преподавания (доступно в панели преподавателя).\n"
    "— Подтверждение записей\n\n"
    "🆘 **Поддержка:**\n"
    "Все административные вопросы решаются через поддержку в боте."
)

STUDENT_INFO_TEXT = (
    "📚 **Информация о занятиях для учеников**\n\n"
    "Занятия проводятся онлайн на платформе **Zoom** или яндекс телемост(ссылку вы получаете перед уроком).\n"
    "Длительность занятия — 60 или 90 минут.\n\n"
    "🎫 **Действующие скидки:**\n"
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


class StudentRecordsStates(StatesGroup):
    viewing = State()


class AdminStates(StatesGroup):
    waiting_commission = State()
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
    add_range = State()
    delete_slot = State()
    range_duration = State()
    range_break = State()


class TutorContactStudentStates(StatesGroup):
    choosing_student = State()
    waiting_message = State()


class SupportUserStates(StatesGroup):
    waiting_message = State()


class SupportAdminReplyStates(StatesGroup):
    waiting_reply = State()


# Новые состояния для переноса учеником и преподавателем
class StudentRescheduleStates(StatesGroup):
    waiting_date = State()
    waiting_time = State()
    waiting_confirmation = State()


class TutorRescheduleStates(StatesGroup):
    waiting_date = State()
    waiting_time = State()
    waiting_confirmation = State()


class TrialBookingStates(StatesGroup):
    choosing_subject = State()
    waiting_date = State()
    waiting_time = State()
    waiting_confirmation = State()


# -------------------- ГЛАВНОЕ МЕНЮ --------------------
async def get_main_menu(user_id: int) -> ReplyKeyboardMarkup:
    is_tutor = await get_tutor_by_telegram_id(user_id) is not None
    is_admin = (user_id == ADMING_ID)

    if is_admin:
        buttons = [
            [KeyboardButton(text="ℹ️ Информация о репетиторах")],
            [KeyboardButton(text="📚 Информация о занятиях")],
            [KeyboardButton(text="📝 Запись на занятие")],
            [KeyboardButton(text="📋 Мои записи")],
            [KeyboardButton(text="💳 Оплата")],
            [KeyboardButton(text="📖 Учебные материалы(Скоро!)")],
            [KeyboardButton(text="✉️ Связь с преподавателем")],
            [KeyboardButton(text="❓ Помощь")],
            # [KeyboardButton(text="👨‍🏫 Панель преподавателя")],
        ]
        buttons.append([KeyboardButton(text="👨‍🏫 Админ-панель")])
        return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    if is_tutor:
        buttons = [
            # [KeyboardButton(text="ℹ️ Информация о репетиторах")],
            [KeyboardButton(text="📚 Информация о занятиях")],
            [KeyboardButton(text="📖 Учебные материалы(Скоро!)")],
            [KeyboardButton(text="👨‍🏫 Панель преподавателя")],
            [KeyboardButton(text="✉️ Связь с учеником")],
            [KeyboardButton(text="🆘 Поддержка")],
            [KeyboardButton(text="❓ Помощь")]
        ]
        return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

    buttons = [
        [KeyboardButton(text="ℹ️ Информация о репетиторах")],
        [KeyboardButton(text="📚 Информация о занятиях")],
        [KeyboardButton(text="📝 Запись на занятие")],
        [KeyboardButton(text="📋 Мои записи")],
        [KeyboardButton(text="💳 Оплата")],
        [KeyboardButton(text="📖 Учебные материалы(Скоро!)")],
        [KeyboardButton(text="✉️ Связь с преподавателем")],
        [KeyboardButton(text="🆘 Поддержка")],
        [KeyboardButton(text="❓ Помощь")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
async def make_tutors_keyboard(callback_prefix: str, back_callback: str = "back_to_menu"):
    tutors = await get_all_tutors()
    buttons = []
    for tid, tdata in tutors.items():
        buttons.append([InlineKeyboardButton(text=tdata["name"], callback_data=f"{callback_prefix}_{tid}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def make_subjects_keyboard(tutor_id: int, back_callback: str = "back_to_menu"):
    tutors = await get_all_tutors()
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
    tutors = await get_all_tutors()
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
    day_of_week = WEEKDAYS[date.weekday()]
    if await is_day_blocked(tutor_id, day_of_week):
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


# ==================== БАЗОВЫЕ ОБРАБОТЧИКИ ====================
@dp.message(Command("start"))
async def Start(message: Message) -> None:
    await message.answer(
        f"Привет, {html.bold(message.from_user.full_name)}! Я онлайн ассистент Никиты Тимуровича. Чем могу помочь?",
        reply_markup=await get_main_menu(message.from_user.id)
    )


@dp.message(F.text.in_(["🔙 Назад"]))
async def main_menu_buttons(message: Message) -> None:
    await message.answer("Главное меню:", reply_markup=await get_main_menu(message.from_user.id))


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await state.clear()
    try:
        await call.message.delete()
    except:
        pass
    try:
        await call.message.answer("Главное меню:", reply_markup=await get_main_menu(call.from_user.id))
    except:
        pass


# ==================== ИНФОРМАЦИЯ О РЕПЕТИТОРАХ ====================
@dp.message(F.text.in_(["ℹ️ Информация о репетиторах"]))
async def repet(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = await make_tutors_keyboard("tutor_info")
    await message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)


@dp.callback_query(F.data == "back_to_tutors")
async def back_to_tutors(call: CallbackQuery):
    keyboard = await make_tutors_keyboard("tutor_info")
    if call.message.content_type == 'photo':
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass  # не удалось удалить – ничего страшного
        await call.message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    else:
        await call.message.edit_text("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    await safe_answer(call)


@dp.callback_query(F.data.startswith("tutor_info_"))
async def show_tutor_info(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    # сбрасываем любые предыдущие FSM, чтобы не мешали
    await state.clear()

    tid = int(call.data.split("_")[-1])
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        await safe_answer(call, "Репетитор не найден", show_alert=True)
        return
    text = tutor["description"] + "\n\nПредметы и цены:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎓 Записаться на пробное занятие", callback_data=f"trials_{tid}")],
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_tutors")]
    ])
    if tutor["photo"]:
        await call.message.delete()
        await call.bot.send_photo(chat_id=call.message.chat.id, photo=tutor["photo"], caption=text,
                                  reply_markup=keyboard)
    else:
        await call.message.edit_text(text, reply_markup=keyboard)



# ==================== ПРОБНОЕ ЗАНЯТИЕ ====================
@dp.callback_query(F.data.startswith("trials_"))
async def start_trial_booking(call: CallbackQuery, state: FSMContext):
    """Точка входа: проверяем, сколько предметов у репетитора."""
    if any(call.data.startswith(p) for p in (
            "trial_subject_", "trial_proceed_", "trial_date_", "trial_slot_"
    )) or call.data == "confirm_trial":
        return

    await safe_answer(call)
    tid = int(call.data.split("_")[1])
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        if call.message.content_type != 'text':
            await call.message.delete()
        await call.message.answer("Репетитор не найден.")
        return

    await state.update_data(tutor_id=tid, tutor_name=tutor["name"])
    subjects = list(tutor["subjects"].keys())

    if len(subjects) == 1:
        subject = subjects[0]
        await state.update_data(subject=subject)
        # Если сообщение – фото, удаляем и показываем кнопку "Продолжить"
        if call.message.content_type != 'text':
            await call.message.delete()
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="▶️ Продолжить", callback_data=f"trial_proceed_{tid}")]
            ])
            await call.message.answer("Ищем доступные слоты на ближайшие 7 дней...", reply_markup=keyboard)
            return
        await call.message.edit_text("Ищем доступные слоты на ближайшие 7 дней...")
        await show_trial_dates(call, state, tid)

    elif len(subjects) > 1:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=subj, callback_data=f"trial_subject_{subj}")] for subj in subjects
        ] + [[InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_tutors")]])

        if call.message.content_type != 'text':
            await call.message.delete()
            await call.message.answer("Выберите предмет для пробного занятия:", reply_markup=keyboard)
            return
        await call.message.edit_text("Выберите предмет для пробного занятия:", reply_markup=keyboard)
        await state.set_state(TrialBookingStates.choosing_subject)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_tutors")]
        ])
        if call.message.content_type != 'text':
            await call.message.delete()
            await call.message.answer("У этого репетитора пока нет предметов.", reply_markup=keyboard)
        else:
            await call.message.edit_text("У этого репетитора пока нет предметов.", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("trial_proceed_"))
async def trial_proceed(call: CallbackQuery, state: FSMContext):
    """Продолжение после удаления фото (один предмет)."""
    await safe_answer(call)
    tid = int(call.data.split("_")[2])
    data = await state.get_data()
    subject = data.get("subject")
    if not subject:
        await call.message.edit_text("Ошибка: предмет не найден.")
        return
    await call.message.edit_text("Ищем доступные слоты на ближайшие 7 дней...")
    await show_trial_dates(call, state, tid)


@dp.callback_query(F.data.startswith("trial_subject_"), StateFilter(TrialBookingStates.choosing_subject))
async def trial_subject_chosen(call: CallbackQuery, state: FSMContext):
    """Выбор предмета из списка."""
    await safe_answer(call)
    subject = call.data.split("trial_subject_", 1)[1]
    await state.update_data(subject=subject)
    data = await state.get_data()
    tid = data["tutor_id"]

    if call.message.content_type != 'text':
        await call.message.delete()
        await call.message.answer("Ищем доступные слоты на ближайшие 7 дней...")
        return

    await call.message.edit_text("Ищем доступные слоты на ближайшие 7 дней...")
    await show_trial_dates(call, state, tid)


async def show_trial_dates(call: CallbackQuery, state: FSMContext, tid: int):
    """Показывает доступные даты на 7 дней вперёд."""
    available_dates = await get_available_dates(tid, days_ahead=7)
    if not available_dates:
        text = "К сожалению, на ближайшие 7 дней у репетитора нет свободных слотов."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К анкете", callback_data=f"tutor_info_{tid}")]
        ])
        if call.message.content_type != 'text':
            await call.message.delete()
            await call.message.answer(text, reply_markup=keyboard)
        else:
            await call.message.edit_text(text, reply_markup=keyboard)
        return

    buttons = []
    for d in available_dates:
        dt = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt.weekday()]]})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"trial_date_{d}")])
    buttons.append([InlineKeyboardButton(text="🔙 К анкете", callback_data=f"tutor_info_{tid}")])

    if call.message.content_type != 'text':
        await call.message.delete()
        await call.message.answer("Выберите дату пробного занятия:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    else:
        await call.message.edit_text("Выберите дату пробного занятия:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TrialBookingStates.waiting_date)


@dp.callback_query(F.data.startswith("trial_date_"), StateFilter(TrialBookingStates.waiting_date))
async def trial_date_chosen(call: CallbackQuery, state: FSMContext):
    """После выбора даты – показать свободные слоты этого дня."""
    await safe_answer(call)
    date_str = call.data.split("trial_date_")[1]
    await state.update_data(date=date_str)
    data = await state.get_data()
    tid = data["tutor_id"]
    slots = await get_available_slots(tid, date_str)
    if not slots:
        await call.message.edit_text(
            "На эту дату нет свободного времени.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К выбору даты", callback_data="back_to_trial_dates")]
            ])
        )
        return

    buttons = [[InlineKeyboardButton(text=s, callback_data=f"trial_slot_{s}")] for s in slots]
    buttons.append([InlineKeyboardButton(text="🔙 К выбору даты", callback_data="back_to_trial_dates")])

    if call.message.content_type != 'text':
        await call.message.delete()
        await call.message.answer("Выберите время:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    else:
        await call.message.edit_text("Выберите время:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TrialBookingStates.waiting_time)


@dp.callback_query(F.data == "back_to_trial_dates", StateFilter("*"))
async def back_to_trial_dates(call: CallbackQuery, state: FSMContext):
    """Возврат к списку дат."""
    data = await state.get_data()
    tid = data["tutor_id"]
    await safe_answer(call)
    await show_trial_dates(call, state, tid)


@dp.callback_query(F.data.startswith("trial_slot_"), StateFilter(TrialBookingStates.waiting_time))
async def trial_slot_chosen(call: CallbackQuery, state: FSMContext):
    """Подтверждение пробного занятия."""
    await safe_answer(call)
    slot = call.data.split("trial_slot_")[1]
    await state.update_data(time_slot=slot)
    data = await state.get_data()
    tid = data["tutor_id"]
    tutors = await get_all_tutors()
    tutor_name = tutors[tid]["name"]

    text = (
        f"🎓 <b>Пробное занятие</b>\n"
        f"👨‍🏫 Репетитор: {tutor_name}\n"
        f"📚 Предмет: {data['subject']}\n"
        f"📅 Дата: {data['date']} (МСК)\n"
        f"🕒 Время: {slot}\n\n"
        f"Подтвердить запись?"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_trial")],
        [InlineKeyboardButton(text="🔙 К выбору времени", callback_data="back_to_trial_dates")]
    ])

    if call.message.content_type != 'text':
        await call.message.delete()
        await call.message.answer(text, reply_markup=keyboard)
    else:
        await call.message.edit_text(text, reply_markup=keyboard)
    await state.set_state(TrialBookingStates.waiting_confirmation)


@dp.callback_query(F.data == "confirm_trial", StateFilter(TrialBookingStates.waiting_confirmation))
async def confirm_trial_booking(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Создание записи и уведомление преподавателя."""
    await safe_answer(call)
    data = await state.get_data()
    tid = data["tutor_id"]
    subject = data["subject"]
    date = data["date"]
    slot = data["time_slot"]
    user = call.from_user
    username = user.username or user.full_name
    uid = user.id

    new_id = await add_booking(tid, uid, username, subject, date, slot)

    booking_msg = (
        f"📝 Новая заявка на пробное занятие (ожидает подтверждения)\n"
        f"👤 Ученик: {username} (ID: {uid})\n"
        f"👨‍🏫 Репетитор: {data['tutor_name']}\n"
        f"📚 Предмет: {subject}\n"
        f"📅 Дата: {date} (МСК)\n"
        f"🕒 Время: {slot} (МСК)"
    )

    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if tutor and tutor.get("telegram_id"):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"tutor_confirm_{new_id}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"tutor_reject_{new_id}")]
        ])
        try:
            await bot.send_message(tutor["telegram_id"], booking_msg, reply_markup=keyboard)
        except:
            pass

    text = "✅ Заявка на пробное занятие отправлена преподавателю. Ожидайте подтверждения."
    if call.message.content_type != 'text':
        await call.message.delete()
        await call.message.answer(text)
    else:
        await call.message.edit_text(text)

    await call.message.answer(
        "Ваша заявка принята и будет рассмотрена.",
        reply_markup=await get_main_menu(call.from_user.id)
    )
    await state.clear()


# ==================== ИНФОРМАЦИЯ О ЗАНЯТИЯХ ====================
@dp.message(F.text.in_(["📚 Информация о занятиях"]))
async def lesson_info(message: types.Message):
    user_id = message.from_user.id
    is_tutor = await get_tutor_by_telegram_id(user_id) is not None
    is_admin = (user_id == ADMING_ID)

    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())

    if is_tutor and not is_admin:
        text = TUTOR_INFO_TEXT
    else:
        text = STUDENT_INFO_TEXT

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer(text, reply_markup=keyboard)


# ==================== ЗАПИСЬ НА ЗАНЯТИЕ ====================
@dp.message(F.text.in_(["📝 Запись на занятие"]))
async def zapis(message: types.Message, state: FSMContext):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = await make_tutors_keyboard("tutor_booking", back_callback="back_to_menu")
    await message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    await state.clear()


@dp.callback_query(F.data.startswith("tutor_booking_"))
async def choose_tutor_booking(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    tid = int(call.data.split("_")[-1])
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        await call.message.edit_text("Ошибка выбора репетитора.")
        return
    await state.update_data(tutor_id=tid, tutor_name=tutor["name"])
    keyboard = await make_subjects_keyboard(tid, back_callback="back_to_tutors_booking")
    await call.message.edit_text("На занятие по какому предмету вы хотите записаться?", reply_markup=keyboard)


@dp.callback_query(F.data == "back_to_tutors_booking")
async def back_to_tutors_booking(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await state.clear()
    keyboard = await make_tutors_keyboard("tutor_booking", back_callback="back_to_menu")
    await call.message.edit_text("Кто из репетиторов Вас интересует?", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("subject_"))
async def subject_chosen(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    parts = call.data.split("_", 2)
    if len(parts) < 3:
        return
    tid = int(parts[1])
    subject = parts[2]
    await state.update_data(subject=subject, tutor_id=tid)
    dates = await get_available_dates(tid)
    if not dates:
        await call.message.edit_text(
            "У этого преподавателя пока нет свободных дат.\nПопробуйте позже или свяжитесь с преподавателем",
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
    await safe_answer(call)
    date_str = call.data.split("_", 1)[1]
    await state.update_data(date=date_str)
    data = await state.get_data()
    tid = data["tutor_id"]
    slots = await get_available_slots(tid, date_str)
    if not slots:
        await call.message.edit_text("На эту дату нет свободного времени.",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[
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
    dates = await get_available_dates(tid)
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
    await safe_answer(call)
    slot = call.data.split("_", 1)[1]
    await state.update_data(time_slot=slot)
    data = await state.get_data()
    tutor_id = data.get("tutor_id")
    # Если tutor_name нет в состоянии, получаем из БД
    if "tutor_name" not in data and tutor_id:
        tutors = await get_all_tutors()
        tutor = tutors.get(tutor_id)
        if tutor:
            data["tutor_name"] = tutor["name"]
            await state.update_data(tutor_name=tutor["name"])
    tutor_name = data.get("tutor_name", "Неизвестный")
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
    await safe_answer(call)
    data = await state.get_data()
    tid = data["tutor_id"]
    tutor_name = data["tutor_name"]
    subject = data["subject"]
    date = data["date"]
    slot = data["time_slot"]
    user = call.from_user
    username = user.username or user.full_name
    uid = user.id

    new_id = await add_booking(tid, uid, username, subject, date, slot)

    booking_msg = (
        f"📝 Новая заявка на занятие (ожидает подтверждения преподавателя)\n"
        f"👤 Ученик: {username} (ID: {uid})\n"
        f"👨‍🏫 Репетитор: {tutor_name}\n"
        f"📚 Предмет: {subject}\n"
        f"📅 Дата: {date} (МСК)\n"
        f"🕒 Время: {slot} (МСК)"
    )
    # await bot.send_message(ADMING_ID, booking_msg)

    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if tutor and tutor.get("telegram_id"):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"tutor_confirm_{new_id}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"tutor_reject_{new_id}")]
        ])
        try:
            await bot.send_message(tutor["telegram_id"], booking_msg, reply_markup=keyboard)
        except:
            pass

    await call.message.edit_text("✅ Заявка отправлена преподавателю. Ожидайте подтверждения.")
    await call.message.answer(
        "Ваша заявка на занятие принята и будет рассмотрена преподавателем.",
        reply_markup=await get_main_menu(call.from_user.id)
    )
    await state.clear()


@dp.callback_query(F.data == "cancel_booking", StateFilter(BookingStates.waiting_confirmation))
async def cancel_booking(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await call.message.edit_text("Запись отменена. Возвращаемся в главное меню.")
    await state.clear()
    await call.message.answer("Главное меню:", reply_markup=await get_main_menu(call.from_user.id))


# ==================== МОИ ЗАПИСИ (УЧЕНИК) ====================
@dp.message(F.text.in_(["📋 Мои записи"]))
async def my_records(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    user_id = message.from_user.id
    bookings = await get_all_bookings()
    user_bookings = []
    for bid, b in bookings.items():
        if b["user_id"] == user_id and b["status"] in ("pending", "confirmed"):
            user_bookings.append((bid, b))

    keyboard_buttons = []
    text_lines = []
    if user_bookings:
        text_lines.append("📋 Ваши записи:\n")
        tutors = await get_all_tutors()
        for bid, b in user_bookings:
            tutor = tutors.get(b["tutor_id"], {"name": "Неизвестный"})
            date_str = b["date"]
            time_str = b["time_slot"]
            dt = parse_booking_time(b)
            now = datetime.now()
            can_act = (dt - now) > timedelta(hours=24)
            status_text = ""
            if b["status"] == "pending":
                status_text = " (ожидает подтверждения)"
                can_act = False
            elif b["status"] == "confirmed":
                status_text = " (подтверждено)"
            act_note = "✅ Можно отменить/перенести" if can_act else "⚠️ Менее 24 часов: действия невозможны"
            text_lines.append(
                f"👨‍🏫 {tutor['name']}\n📚 {b['subject']}\n📅 {date_str} (МСК) 🕒 {time_str}{status_text}\n{act_note}"
            )
            if can_act and b["status"] == "confirmed":
                keyboard_buttons.append([InlineKeyboardButton(
                    text=f"🔄 Перенести: {tutor['name']} {date_str} {time_str}",
                    callback_data=f"reschedule_student_{bid}"
                )])
            if can_act:
                keyboard_buttons.append([InlineKeyboardButton(
                    text=f"❌ Отменить: {tutor['name']} {date_str} {time_str}",
                    callback_data=f"cancel_student_{bid}"
                )])
    else:
        text_lines.append("У вас пока нет активных записей.\n")

    keyboard_buttons.append([InlineKeyboardButton(text="📊 Статистика", callback_data="student_stats")])
    keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    await message.answer("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons))


@dp.callback_query(F.data.startswith("cancel_student_"))
async def cancel_student_booking(call: CallbackQuery, bot: Bot):
    await safe_answer(call)
    bid = int(call.data.split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking:
        await call.message.edit_text("Запись не найдена.")
        return

    if booking.get("channel_msg_id") and RECORDS_CHANNEL_ID:
        try:
            await bot.delete_message(chat_id=RECORDS_CHANNEL_ID, message_id=booking["channel_msg_id"])
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение из канала: {e}")

    dt = parse_booking_time(booking)
    if (dt - datetime.now()) <= timedelta(hours=24):
        await call.message.edit_text("Слишком поздно отменять. Стоимость не возвращается.")
        return
    # Отменяем
    await update_booking(bid, status="cancelled")
    # Уведомления
    student_id = booking["user_id"]
    tutor_id = booking["tutor_id"]
    tutors = await get_all_tutors()
    tutor_name = tutors.get(tutor_id, {}).get("name", "Неизвестный")
    msg_student = "✅ Вы отменили занятие."
    await bot.send_message(student_id, msg_student)
    if tutor_id and (tutor_tg := tutors.get(tutor_id, {}).get("telegram_id")):
        msg_tutor = (
            f"❌ Ученик {booking['username']} отменил занятие:\n"
            f"📚 {booking['subject']}\n📅 {booking['date']} (МСК) 🕒 {booking['time_slot']}"
        )
        try:
            await bot.send_message(tutor_tg, msg_tutor)
        except:
            pass
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К моим записям", callback_data="back_to_my_records")]
    ])
    await call.message.edit_text("✅ Запись отменена.", reply_markup=keyboard)

@dp.callback_query(F.data == "student_stats")
async def show_student_stats(call: CallbackQuery):
    await safe_answer(call)
    user_id = call.from_user.id
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К моим записям", callback_data="back_to_my_records")],
    ])
    await call.message.edit_text(text, reply_markup=keyboard)


@dp.callback_query(F.data == "back_to_my_records")
async def back_to_my_records(call: CallbackQuery):
    await call.message.edit_text("Возврат в главное меню...")
    await call.message.answer("Главное меню:", reply_markup=await get_main_menu(call.from_user.id))
    await safe_answer(call)


# ==================== ПЕРЕНОС УЧЕНИКОМ ====================
@dp.callback_query(F.data.startswith("reschedule_student_"))
async def student_reschedule_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    bid = int(call.data.split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking or booking["status"] != "confirmed":
        await call.message.edit_text("Запись недоступна для переноса.")
        return
    dt = parse_booking_time(booking)
    if (dt - datetime.now()) <= timedelta(hours=24):
        await call.message.edit_text("Перенос возможен не позднее чем за 24 часа.")
        return
    # Сохраняем данные старой записи
    await state.update_data(
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
        await call.message.edit_text("У преподавателя нет свободных дат для переноса.")
        return
    buttons = []
    for d in dates:
        dt_date = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt_date.weekday()]]})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"reschedule_date_{d}")])
    buttons.append([InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_menu")])
    await call.message.edit_text("Выберите новую дату:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(StudentRescheduleStates.waiting_date)


@dp.callback_query(F.data.startswith("reschedule_date_"), StateFilter(StudentRescheduleStates.waiting_date))
async def student_reschedule_date(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    date_str = call.data.split("reschedule_date_")[1]
    await state.update_data(new_date=date_str)
    data = await state.get_data()
    tid = data["tutor_id"]
    old_bid = data["old_booking_id"]
    slots = await get_available_slots(tid, date_str, exclude_booking_id=old_bid)
    if not slots:
        await call.message.edit_text("На эту дату нет свободных слотов.",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                         [InlineKeyboardButton(text="🔙 К выбору даты",
                                                               callback_data="back_to_reschedule_date")]
                                     ]))
        return
    buttons = [[InlineKeyboardButton(text=s, callback_data=f"reschedule_slot_{s}")] for s in slots]
    buttons.append([InlineKeyboardButton(text="🔙 К выбору даты", callback_data="back_to_reschedule_date")])
    await call.message.edit_text("Выберите новое время:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(StudentRescheduleStates.waiting_time)


@dp.callback_query(F.data == "back_to_reschedule_date", StateFilter("*"))
async def back_to_reschedule_date(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tid = data["tutor_id"]
    dates = await get_available_dates(tid)
    buttons = []
    for d in dates:
        dt_date = datetime.strptime(d, "%d.%m.%Y")
        label = f"{d} ({WEEKDAY_NAMES[WEEKDAYS[dt_date.weekday()]]})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"reschedule_date_{d}")])
    buttons.append([InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_menu")])
    await call.message.edit_text("Выберите новую дату:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(StudentRescheduleStates.waiting_date)


@dp.callback_query(F.data.startswith("reschedule_slot_"), StateFilter(StudentRescheduleStates.waiting_time))
async def student_reschedule_slot(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    slot = call.data.split("reschedule_slot_")[1]
    await state.update_data(new_time=slot)
    data = await state.get_data()
    text = (
        f"Перенос занятия:\n"
        f"👨‍🏫 Репетитор: {data.get('tutor_name', '')}\n"
        f"📚 Предмет: {data['subject']}\n"
        f"Старая дата/время: {data['old_date']} {data['old_time']}\n"
        f"Новая дата/время: {data['new_date']} {slot}\n\nПодтвердить перенос?"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить перенос", callback_data="confirm_student_reschedule")],
        [InlineKeyboardButton(text="🔙 Назад к выбору времени", callback_data="back_to_reschedule_date")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard)
    await state.set_state(StudentRescheduleStates.waiting_confirmation)


@dp.callback_query(F.data == "confirm_student_reschedule", StateFilter(StudentRescheduleStates.waiting_confirmation))
async def confirm_student_reschedule(call: CallbackQuery, state: FSMContext, bot: Bot):
    await safe_answer(call)
    data = await state.get_data()
    old_bid = data["old_booking_id"]
    tid = data["tutor_id"]
    new_date = data["new_date"]
    new_time = data["new_time"]
    subject = data["subject"]
    student_id = data["student_id"]
    student_username = data["student_username"]

    # --- 1. Удаляем старое сообщение из канала ---
    old_booking = (await get_all_bookings()).get(old_bid)
    if old_booking and old_booking.get("channel_msg_id") and RECORDS_CHANNEL_ID:
        try:
            await bot.delete_message(chat_id=RECORDS_CHANNEL_ID, message_id=old_booking["channel_msg_id"])
        except Exception as e:
            logging.warning(f"Не удалось удалить старое сообщение: {e}")

    # --- 2. Отменяем старую запись ---
    await update_booking(old_bid, status="cancelled")

    # --- 3. Создаём новую (pending) ---
    new_id = await add_booking(tid, student_id, student_username, subject, new_date, new_time)

    # --- 4. Уведомляем преподавателя (с кнопками) ---
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    tutor_name = tutor["name"] if tutor else "Неизвестный"
    tutor_tg = tutor.get("telegram_id") if tutor else None

    notify_tutor = (
        f"🔄 Ученик {student_username} перенёс занятие.\n"
        f"Предмет: {subject}\n"
        f"Было: {data['old_date']} {data['old_time']}\n"
        f"Новая заявка: {new_date} {new_time} (ожидает подтверждения)"
    )
    if tutor_tg:
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"tutor_confirm_{new_id}")],
                [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"tutor_reject_{new_id}")]
            ])
            await bot.send_message(tutor_tg, notify_tutor, reply_markup=keyboard)
        except:
            pass

    # --- 5. Уведомляем ученика ---
    await bot.send_message(student_id,
                           f"✅ Заявка на перенос отправлена преподавателю. Новое время: {new_date} {new_time}.")

    await call.message.edit_text("Перенос выполнен. Ожидайте подтверждения нового времени.")
    await call.message.answer("Главное меню:", reply_markup=await get_main_menu(call.from_user.id))
    await state.clear()


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
    await safe_answer(call)


@dp.callback_query(F.data == "qr")
async def qr(call: CallbackQuery):
    await call.message.edit_text("📱 Сканируйте QR-код для оплаты в приложении вашего банка",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                     [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_pay")]
                                 ]))
    await safe_answer(call)


@dp.callback_query(F.data == "card")
async def card(call: CallbackQuery):
    await call.message.edit_text("💳 Переходите по ссылке и следуйте дальнейшим инструкциям",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                     [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_pay")]
                                 ]))
    await safe_answer(call)


@dp.callback_query(F.data == "sbp")
async def sbp(call: CallbackQuery):
    await call.message.edit_text(
        "📲 Перевод выполняйте, указывая предмет и дату занятия, по номеру +7(933)120-96-03 на Т-банк",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_pay")]
        ]))
    await safe_answer(call)


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
    await call.message.edit_text("🎥 Видеоматериалы (записи реакций и явлений)",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                     [InlineKeyboardButton(text="🧪 Химия", callback_data="videh")],
                                     [InlineKeyboardButton(text="⚛️ Физика", callback_data="videf")],
                                     [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_mat")]
                                 ]))


@dp.callback_query(F.data.startswith("bookh"))
async def bookh(call: CallbackQuery):
    await safe_answer(call, "Скоро здесь будут пособия по химии", show_alert=True)


@dp.callback_query(F.data.startswith("bookf"))
async def bookf(call: CallbackQuery):
    await safe_answer(call, "Скоро здесь будут пособия по физике", show_alert=True)


@dp.callback_query(F.data.startswith("videh"))
async def videh(call: CallbackQuery):
    await safe_answer(call, "Скоро здесь будут видео по химии", show_alert=True)


@dp.callback_query(F.data.startswith("videf"))
async def videf(call: CallbackQuery):
    await safe_answer(call, "Скоро здесь будут видео по физике", show_alert=True)


# ==================== СВЯЗЬ С ПРЕПОДАВАТЕЛЕМ ====================
@dp.message(F.text.in_(["✉️ Связь с преподавателем"]))
async def svyaz(message: types.Message, state: FSMContext):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = await make_tutors_keyboard("msg_tutor", back_callback="back_to_menu")
    await message.answer("Выберите преподавателя, которому хотите написать:", reply_markup=keyboard)
    await state.set_state(ContactStates.choosing_tutor)


@dp.callback_query(F.data.startswith("msg_tutor_"), StateFilter(ContactStates.choosing_tutor))
async def choose_msg_tutor(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    tid = int(call.data.split("_")[-1])
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        await call.message.edit_text("Преподаватель не найден.")
        return
    await state.update_data(msg_tutor_id=tid, msg_tutor_name=tutor["name"])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_msg_to_tutor")]
    ])
    await call.message.edit_text(
        f"Вы пишете преподавателю {tutor['name']}.\nВведите ваше сообщение:",
        reply_markup=keyboard
    )
    await state.set_state(ContactStates.waiting_message)

@dp.callback_query(F.data == "cancel_msg_to_tutor", StateFilter(ContactStates.waiting_message))
async def cancel_msg_to_tutor(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await state.clear()
    # возвращаемся к выбору преподавателя
    keyboard = await make_tutors_keyboard("msg_tutor", back_callback="back_to_menu")
    await call.message.edit_text("Выберите преподавателя, которому хотите написать:", reply_markup=keyboard)

@dp.message(ContactStates.waiting_message)
async def send_message_to_tutor(message: Message, state: FSMContext, bot: Bot):
    user = message.from_user
    username = user.username or user.full_name
    data = await state.get_data()
    tid = data["msg_tutor_id"]
    tutor_name = data["msg_tutor_name"]
    text = message.text.strip()

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
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if tutor and tutor.get("telegram_id"):
        try:
            await bot.send_message(tutor["telegram_id"], forward_msg, reply_markup=reply_markup)
        except:
            pass

    await message.answer("✅ Сообщение отправлено. Ожидайте ответа.",
                         reply_markup=await get_main_menu(message.from_user.id))
    await state.clear()


@dp.callback_query(F.data.startswith("reply_"))
async def process_reply_button(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    student_id = int(call.data.split("_")[1])
    await state.update_data(reply_student_id=student_id)
    await call.message.answer("Введите ваш ответ (текст):")
    await state.set_state(ContactStates.waiting_reply)


@dp.message(ContactStates.waiting_reply)
async def send_reply_to_student(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    student_id = data["reply_student_id"]
    reply_text = f"📬 Ответ от преподавателя:\n{message.text}"
    try:
        await bot.send_message(student_id, reply_text)
        await message.answer("✅ Ответ отправлен ученику.", reply_markup=await get_main_menu(message.from_user.id))
    except:
        await message.answer("⚠️ Не удалось отправить ответ (возможно, ученик заблокировал бота).",
                             reply_markup=await get_main_menu(message.from_user.id))
    await state.clear()


# ==================== СВЯЗЬ ПРЕПОДАВАТЕЛЯ С УЧЕНИКОМ ====================
@dp.message(F.text.in_(["✉️ Связь с учеником"]))
async def tutor_contact_student_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    tutor_id = await get_tutor_by_telegram_id(user_id)
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

    buttons = []
    for uid, name in students.items():
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"tutorcontactstudent_{uid}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    await message.answer("Выберите ученика:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorContactStudentStates.choosing_student)


@dp.callback_query(F.data.startswith("tutor_contact_student_"), StateFilter(TutorContactStudentStates.choosing_student))
async def tutor_contact_student_chosen(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    student_id = int(call.data.split("_")[-1])
    await state.update_data(tutor_contact_student_id=student_id)
    student_username = "Неизвестный"
    bookings = await get_all_bookings()
    for b in bookings.values():
        if b["user_id"] == student_id:
            student_username = b["username"]
            break
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_tutor_msg_to_student")]
    ])
    await call.message.edit_text(
        f"Вы пишете ученику {student_username}. Введите сообщение:",
        reply_markup=keyboard
    )
    await state.set_state(TutorContactStudentStates.waiting_message)

@dp.callback_query(F.data == "cancel_tutor_msg_to_student", StateFilter(TutorContactStudentStates.waiting_message))
async def cancel_tutor_msg_to_student(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await state.clear()
    # возвращаемся в панель преподавателя
    tid = await get_tutor_by_telegram_id(call.from_user.id)
    if tid:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои ученики", callback_data=f"tutor_students_{tid}")],
            [InlineKeyboardButton(text="⚙️ Настроить расписание", callback_data=f"tutor_schedule_{tid}")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data=f"tutor_stats_{tid}")],
            [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
        ])
        await call.message.edit_text("Панель преподавателя:", reply_markup=keyboard)
    else:
        await call.message.edit_text("Главное меню:", reply_markup=await get_main_menu(call.from_user.id))


@dp.message(TutorContactStudentStates.waiting_message)
async def tutor_send_message_to_student(message: Message, state: FSMContext, bot: Bot):
    user = message.from_user
    data = await state.get_data()
    student_id = data["tutor_contact_student_id"]

    tutor_id = await get_tutor_by_telegram_id(user.id)
    if not tutor_id:
        await message.answer("Ошибка идентификации преподавателя.")
        return
    tutors = await get_all_tutors()
    tutor = tutors.get(tutor_id, {})
    tutor_name = tutor.get("name", "Преподаватель")

    forward_msg = (
        f"📨 Сообщение от преподавателя {tutor_name}:\n\n"
        f"{message.text}"
    )
    try:
        await bot.send_message(student_id, forward_msg)
        await message.answer("✅ Сообщение отправлено ученику.", reply_markup=await get_main_menu(user.id))
    except Exception:
        await message.answer("⚠️ Не удалось отправить сообщение (возможно, ученик заблокировал бота).",
                             reply_markup=await get_main_menu(user.id))
    await state.clear()


# ==================== ПОДДЕРЖКА ====================
@dp.message(F.text.in_(["🆘 Поддержка"]))
async def support_start(message: types.Message, state: FSMContext):
    await message.answer("Переходим в раздел...",
                         reply_markup=ReplyKeyboardRemove())

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_support")]
        ])
    await message.answer(
            "Опишите вашу проблему или вопрос. Администратор свяжется с вами.",
            reply_markup=keyboard
        )
    await state.set_state(SupportUserStates.waiting_message)

@dp.callback_query(F.data == "cancel_support", StateFilter(SupportUserStates.waiting_message))
async def cancel_support(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await state.clear()
    await call.message.edit_text("Обращение отменено.")
    await call.message.answer("Главное меню:", reply_markup=await get_main_menu(call.from_user.id))

@dp.message(SupportUserStates.waiting_message)
async def support_message_to_admin(message: Message, state: FSMContext, bot: Bot):
    user = message.from_user
    username = user.username or user.full_name
    uid = user.id
    text = message.text.strip()

    forward_msg = (
        f"🆘 Сообщение в поддержку от {username} (ID: {uid}):\n\n"
        f"{text}"
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Ответить", callback_data=f"support_reply_{uid}")]
    ])
    await bot.send_message(ADMING_ID, forward_msg, reply_markup=reply_markup)
    await message.answer("✅ Ваше сообщение отправлено администратору. Ожидайте ответа.",
                         reply_markup=await get_main_menu(uid))
    await state.clear()


@dp.callback_query(F.data.startswith("support_reply_"))
async def support_reply_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if call.from_user.id != ADMING_ID:
        await safe_answer(call, "⛔ Только администратор может отвечать на обращения.", show_alert=True)
        return
    student_id = int(call.data.split("_")[-1])
    await state.update_data(support_reply_student_id=student_id)
    await call.message.answer("Введите ответ пользователю:")
    await state.set_state(SupportAdminReplyStates.waiting_reply)


@dp.message(SupportAdminReplyStates.waiting_reply)
async def support_send_reply(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    student_id = data["support_reply_student_id"]
    reply_text = f"📬 Ответ от администратора:\n{message.text}"
    try:
        await bot.send_message(student_id, reply_text)
        await message.answer("✅ Ответ отправлен пользователю.", reply_markup=await get_main_menu(message.from_user.id))
    except Exception:
        await message.answer("⚠️ Не удалось отправить ответ (возможно, пользователь заблокировал бота).",
                             reply_markup=await get_main_menu(message.from_user.id))
    await state.clear()


# ==================== АДМИН-ПАНЕЛЬ ====================
def admin_actions_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить репетитора", callback_data="admin_add")],
        [InlineKeyboardButton(text="✏️ Редактировать репетитора", callback_data="admin_edit_list")],
        [InlineKeyboardButton(text="❌ Удалить репетитора", callback_data="admin_delete_list")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])


@dp.message(F.text.in_(["👨‍🏫 Админ-панель"]))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMING_ID:
        await message.answer("⛔ Доступ запрещён.")
        return
    await message.answer("Админ-панель управления репетиторами", reply_markup=ReplyKeyboardRemove())
    await message.answer("Выберите действие:", reply_markup=admin_actions_keyboard())


@dp.callback_query(F.data == "admin_panel_open")
async def open_admin_panel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await call.message.answer("Админ-панель управления репетиторами", reply_markup=ReplyKeyboardRemove())
    await call.message.answer("Выберите действие:", reply_markup=admin_actions_keyboard())
    await safe_answer(call)


# ==================== АДМИН-СТАТИСТИКА ====================
@dp.callback_query(F.data == "admin_stats")
async def admin_stats_menu(call: CallbackQuery):
    await safe_answer(call)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍🏫 Статистика по репетиторам", callback_data="admin_stats_tutors")],
        [InlineKeyboardButton(text="👤 Статистика по ученикам", callback_data="admin_stats_students")],
        [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel_open")]
    ])
    await call.message.edit_text("📊 Административная статистика\nВыберите раздел:", reply_markup=keyboard)


@dp.callback_query(F.data == "admin_stats_tutors")
async def admin_stats_tutors_overview(call: CallbackQuery):
    await safe_answer(call)
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
    buttons = [[InlineKeyboardButton(text=f"{y}-{m:02d}", callback_data=f"admin_stats_tutors_month_{y}_{m}")] for y, m
               in months]
    buttons.append([InlineKeyboardButton(text="🔙 К разделам статистики", callback_data="admin_stats")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("admin_stats_tutors_month_"))
async def admin_stats_tutors_month(call: CallbackQuery):
    parts = call.data.split("_")
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К общей статистике", callback_data="admin_stats_tutors")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard)


@dp.callback_query(F.data == "admin_stats_students")
async def admin_stats_students(call: CallbackQuery):
    await safe_answer(call)
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К разделам статистики", callback_data="admin_stats")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard)


# --- Добавление репетитора ---
@dp.callback_query(F.data == "admin_add")
async def admin_add_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
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
    await message.answer("Введите процент комиссии (целое число, по умолчанию 15):")
    await state.set_state(AdminStates.waiting_commission)


@dp.message(AdminStates.waiting_commission)
async def admin_add_commission(message: Message, state: FSMContext):
    try:
        comm = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число.")
        return
    await state.update_data(commission_percent=comm)
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
    await message.answer(f"Предмет «{temp_subject}» с ценой {price} руб. добавлен. Добавить ещё предмет?",
                         reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_subject_name)


@dp.callback_query(F.data == "add_another_subject", StateFilter(AdminStates.waiting_subject_name))
async def add_another_subject(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await call.message.edit_text("Введите название следующего предмета:")


@dp.callback_query(F.data == "finish_adding_subjects", StateFilter(AdminStates.waiting_subject_name))
async def finish_adding_subjects(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    data = await state.get_data()
    new_id = await add_tutor(
        name=data["name"],
        photo=data.get("photo", ""),
        telegram_id=data.get("telegram_id"),
        description=data["description"],
        commission_percent=data.get("commission_percent", 15)
    )
    subjects = data.get("subjects", {})
    for subj_name, subj_price in subjects.items():
        await add_subject(new_id, subj_name, subj_price)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 В админ-панель", callback_data="admin_panel_open")]
    ])
    await call.message.edit_text(f"✅ Репетитор «{data['name']}» успешно добавлен (ID {new_id}).", reply_markup=keyboard)
    await state.clear()


# --- Редактирование репетитора ---
@dp.callback_query(F.data == "admin_edit_list")
async def admin_edit_list(call: CallbackQuery):
    await safe_answer(call)
    tutors = await get_all_tutors()
    if not tutors:
        await call.message.edit_text("Нет репетиторов для редактирования.",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                         [InlineKeyboardButton(text="📂 В админ-панель",
                                                               callback_data="admin_panel_open")]
                                     ]))
        return
    keyboard = await make_tutors_keyboard("edit_tutor", back_callback="admin_panel_open")
    await call.message.edit_text("Выберите репетитора для редактирования:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("edit_tutor_"))
async def edit_tutor_choice(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    tid = int(call.data.split("_")[-1])
    await state.update_data(edit_tutor_id=tid)
    tutors = await get_all_tutors()
    tutor = tutors[tid]
    info = f"Редактирование: {tutor['name']}\n\nЧто хотите изменить?"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить имя", callback_data="edit_name")],
        [InlineKeyboardButton(text="Изменить описание", callback_data="edit_desc")],
        [InlineKeyboardButton(text="Изменить фото", callback_data="edit_photo")],
        [InlineKeyboardButton(text="Изменить Telegram ID", callback_data="edit_telegram_id")],
        [InlineKeyboardButton(text="📚 Управление предметами", callback_data="manage_subjects")],
        [InlineKeyboardButton(text="💰 Изменить комиссию", callback_data="edit_commission")],
        [InlineKeyboardButton(text="🔙 К списку", callback_data="admin_edit_list")]
    ])
    await call.message.edit_text(info, reply_markup=keyboard)


@dp.callback_query(F.data == "edit_commission", StateFilter("*"))
async def edit_commission_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await state.update_data(edit_field="commission")
    await call.message.edit_text("Введите новый процент комиссии (целое число):")
    await state.set_state(AdminStates.waiting_new_value)
    # ВАЖНО: здесь использовалась необъявленная переменная field, но мы оставляем как есть, только заменили вызов answer


@dp.callback_query(F.data.startswith("edit_"), StateFilter("*"))
async def edit_field_choice(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    field = call.data.split("_", 1)[1]
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

    kwargs = {}
    if field == "photo":
        if message.photo:
            kwargs["photo"] = message.photo[-1].file_id
        else:
            kwargs["photo"] = ""
    elif field == "name":
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
    await update_tutor(tid, **kwargs)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 В админ-панель", callback_data="admin_panel_open")]
    ])
    await message.answer("✅ Изменения сохранены.", reply_markup=keyboard)
    await state.clear()


# --- Управление предметами ---
@dp.callback_query(F.data == "manage_subjects", StateFilter("*"))
async def manage_subjects(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    tutors = await get_all_tutors()
    if not tid or tid not in tutors:
        await safe_answer(call, "Ошибка", show_alert=True)
        return
    await show_manage_subjects_menu(call, state, tid)


@dp.callback_query(F.data == "back_to_edit_tutor", StateFilter(AdminStates.managing_subjects))
async def back_to_edit_tutor(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    tutors = await get_all_tutors()
    if not tid or tid not in tutors:
        await safe_answer(call, "Ошибка", show_alert=True)
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
    await safe_answer(call)
    await call.message.edit_text("Введите название нового предмета:")
    await state.set_state(AdminStates.adding_subject_name)


@dp.message(AdminStates.adding_subject_name)
async def process_adding_subject_name(message: Message, state: FSMContext):
    name = message.text.strip()
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    tutors = await get_all_tutors()
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
    await add_subject(tid, name, price)
    await message.answer(f"✅ Предмет «{name}» добавлен с ценой {price} руб.")
    await show_manage_subjects_menu(message, state, tid)


@dp.callback_query(F.data.startswith("editsubj_"), StateFilter(AdminStates.managing_subjects))
async def edit_subject_menu(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
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
    await safe_answer(call)
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    await show_manage_subjects_menu(call, state, tid)


@dp.callback_query(F.data == "editsubj_name", StateFilter(AdminStates.editing_subject_choice))
async def edit_subject_name_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await call.message.edit_text("Введите новое название предмета:")
    await state.set_state(AdminStates.editing_subject_name_state)


@dp.message(AdminStates.editing_subject_name_state)
async def process_new_subject_name(message: Message, state: FSMContext):
    new_name = message.text.strip()
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    old_name = data.get("edit_subject_name")
    tutors = await get_all_tutors()
    if tid and old_name in tutors[tid]["subjects"]:
        if new_name != old_name and new_name in tutors[tid]["subjects"]:
            await message.answer("Предмет с таким названием уже существует. Введите другое.")
            return
        await update_subject(tid, old_name, new_name=new_name)
    await message.answer(f"✅ Название предмета изменено на «{new_name}».")
    await state.update_data(edit_subject_name=None)
    await show_manage_subjects_menu(message, state, tid)


@dp.callback_query(F.data == "editsubj_price", StateFilter(AdminStates.editing_subject_choice))
async def edit_subject_price_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
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
    await update_subject(tid, subj, new_price=new_price)
    await message.answer(f"✅ Цена для предмета «{subj}» изменена на {new_price} руб.")
    await show_manage_subjects_menu(message, state, tid)


@dp.callback_query(F.data == "editsubj_delete", StateFilter(AdminStates.editing_subject_choice))
async def delete_subject_confirm(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
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
    await safe_answer(call)
    data = await state.get_data()
    tid = data.get("edit_tutor_id")
    subj = data.get("edit_subject_name")
    await delete_subject(tid, subj)
    await call.message.edit_text(f"✅ Предмет «{subj}» удалён.")
    await show_manage_subjects_menu(call, state, tid)


# --- Удаление репетитора ---
@dp.callback_query(F.data == "admin_delete_list")
async def admin_delete_list(call: CallbackQuery):
    await safe_answer(call)
    tutors = await get_all_tutors()
    if not tutors:
        await call.message.edit_text("Нет репетиторов для удаления.",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                         [InlineKeyboardButton(text="📂 В админ-панель",
                                                               callback_data="admin_panel_open")]
                                     ]))
        return
    keyboard = await make_tutors_keyboard("del_tutor", back_callback="admin_panel_open")
    await call.message.edit_text("Выберите репетитора для удаления:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("del_tutor_"))
async def delete_tutor_confirm(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    tid = int(call.data.split("_")[-1])
    await state.update_data(del_tutor_id=tid)
    tutors = await get_all_tutors()
    tutor = tutors[tid]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_delete_list")]
    ])
    await call.message.edit_text(f"Удалить репетитора «{tutor['name']}»?", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_delete_confirm)


@dp.callback_query(F.data == "confirm_delete", StateFilter(AdminStates.waiting_delete_confirm))
async def confirm_delete(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    data = await state.get_data()
    tid = data["del_tutor_id"]
    tutors = await get_all_tutors()
    name = tutors[tid]["name"]
    await delete_tutor(tid)
    await call.message.edit_text(f"✅ Репетитор «{name}» удалён.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 В админ-панель", callback_data="admin_panel_open")]
    ]))
    await state.clear()


# ==================== ПАНЕЛЬ ПРЕПОДАВАТЕЛЯ ====================
@dp.message(F.text.in_(["👨‍🏫 Панель преподавателя"]))
async def tutor_panel(message: types.Message):
    user_id = message.from_user.id
    tutor_id = await get_tutor_by_telegram_id(user_id)
    if not tutor_id:
        await message.answer("⛔ Вы не зарегистрированы как преподаватель.")
        return
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Моя анкета", callback_data=f"tutor_profile_{tutor_id}")],
        [InlineKeyboardButton(text="📋 Мои ученики", callback_data=f"tutor_students_{tutor_id}")],
        [InlineKeyboardButton(text="⚙️ Настроить расписание", callback_data=f"tutor_schedule_{tutor_id}")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data=f"tutor_stats_{tutor_id}")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Панель преподавателя:", reply_markup=keyboard)


async def count_student_lessons(tutor_id: int, user_id: int) -> int:
    bookings = await get_all_bookings()
    count = 0
    for b in bookings.values():
        if b["tutor_id"] == tutor_id and b["user_id"] == user_id and b["status"] in ("confirmed", "completed"):
            count += 1
    return count


@dp.callback_query(F.data.startswith("tutor_students_"))
async def show_students(call: CallbackQuery, bot: Bot):
    tid = int(call.data.split("_")[-1])
    bookings = await get_all_bookings()
    tutors = await get_all_tutors()
    students = {}
    for bid, b in bookings.items():
        if b["tutor_id"] == tid and b["status"] in ("pending", "confirmed"):
            uid = b["user_id"]
            students.setdefault(uid, {"username": b["username"], "bookings": []})
            students[uid]["bookings"].append((bid, b))
    if not students:
        await call.message.edit_text("У вас пока нет активных записей.",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                         [InlineKeyboardButton(text="🔙 Назад",
                                                               callback_data=f"back_to_tutor_panel_{tid}")]
                                     ]))
        return
    text = "📋 Ваши ученики:\n\n"
    keyboard = []
    for uid, sdata in students.items():
        lessons_count = await count_student_lessons(tid, uid)
        text += f"👤 {sdata['username']} (занятий: {lessons_count})\n"
        for bid, b in sdata["bookings"]:
            status_emoji = "⏳" if b["status"] == "pending" else "✅"
            text += f"  {status_emoji} {b['date']} (МСК) {b['time_slot']} – {b['subject']}\n"
            if b["status"] == "pending":
                keyboard.append([
                    InlineKeyboardButton(text=f"✅ Подтвердить {b['username']} {b['date']} {b['time_slot']}",
                                         callback_data=f"tutor_confirm_{bid}"),
                    InlineKeyboardButton(text=f"❌ Отклонить", callback_data=f"tutor_reject_{bid}")
                ])
            elif b["status"] == "confirmed":
                dt = datetime.strptime(b["date"] + " " + b["time_slot"].split("-")[0], "%d.%m.%Y %H:%M")
                if (dt - datetime.now()) > timedelta(hours=24):
                    keyboard.append([
                        InlineKeyboardButton(text=f"❌ Отменить", callback_data=f"tutor_cancel_{bid}"),
                        InlineKeyboardButton(text=f"🔄 Перенести", callback_data=f"tutor_reschedule_{bid}")
                    ])
        text += "\n"
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_tutor_panel_{tid}")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


@dp.callback_query(F.data.startswith("back_to_tutor_panel_"))
async def back_to_tutor_panel(call: CallbackQuery):
    tid = int(call.data.split("_")[-1])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Моя анкета", callback_data=f"tutor_profile_{tid}")],
        [InlineKeyboardButton(text="📋 Мои ученики", callback_data=f"tutor_students_{tid}")],
        [InlineKeyboardButton(text="⚙️ Настроить расписание", callback_data=f"tutor_schedule_{tid}")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data=f"tutor_stats_{tid}")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    try:
        await call.message.edit_text("Панель преподавателя:", reply_markup=keyboard)
    except TelegramBadRequest:
        # Редактирование не удалось (например, сообщение было фото) — удаляем и отправляем новое
        try:
            await call.message.delete()
        except TelegramBadRequest:
            pass
        await call.message.answer("Панель преподавателя:", reply_markup=keyboard)
    await safe_answer(call)


# --- Отмена преподавателем ---
@dp.callback_query(F.data.startswith("tutor_cancel_"))
async def tutor_cancel_booking(call: CallbackQuery, bot: Bot):
    await safe_answer(call)
    bid = int(call.data.split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking or booking["status"] != "confirmed":
        await call.message.edit_text("Невозможно отменить.")
        return

    if booking.get("channel_msg_id") and RECORDS_CHANNEL_ID:
        try:
            await bot.delete_message(chat_id=RECORDS_CHANNEL_ID, message_id=booking["channel_msg_id"])
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение из канала: {e}")

    dt = parse_booking_time(booking)
    if (dt - datetime.now()) <= timedelta(hours=24):
        await call.message.edit_text("Отмена менее чем за 24 часа невозможна.")
        return
    await update_booking(bid, status="cancelled")
    student_id = booking["user_id"]
    tutors = await get_all_tutors()
    tutor_name = tutors.get(booking["tutor_id"], {}).get("name", "Преподаватель")
    msg = f"❌ Преподаватель {tutor_name} отменил занятие {booking['date']} {booking['time_slot']} по предмету «{booking['subject']}»."
    await bot.send_message(student_id, msg)
    tid = booking["tutor_id"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К списку учеников", callback_data=f"tutor_students_{tid}")]
    ])
    await call.message.edit_text("✅ Занятие отменено.", reply_markup=keyboard)


# --- Перенос преподавателем ---
@dp.callback_query(F.data.startswith("tutor_reschedule_"))
async def tutor_reschedule_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    bid = int(call.data.split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking or booking["status"] != "confirmed":
        await call.message.edit_text("Невозможно перенести.")
        return
    dt = parse_booking_time(booking)
    if (dt - datetime.now()) <= timedelta(hours=24):
        await call.message.edit_text("Перенос менее чем за 24 часа невозможен.")
        return
    await state.update_data(
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
        await call.message.edit_text("Нет доступных дат для переноса.")
        return
    buttons = [[InlineKeyboardButton(
        text=f"{d} ({WEEKDAY_NAMES[WEEKDAYS[datetime.strptime(d, '%d.%m.%Y').weekday()]]})",
        callback_data=f"t_reschedule_date_{d}")] for d in dates]
    buttons.append([InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_menu")])
    await call.message.edit_text("Выберите новую дату:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorRescheduleStates.waiting_date)


@dp.callback_query(F.data.startswith("t_reschedule_date_"), StateFilter(TutorRescheduleStates.waiting_date))
async def tutor_reschedule_date(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    date_str = call.data.split("t_reschedule_date_")[1]
    await state.update_data(new_date=date_str)
    data = await state.get_data()
    tid = data["tutor_id"]
    old_bid = data["old_booking_id"]
    slots = await get_available_slots(tid, date_str, exclude_booking_id=old_bid)
    if not slots:
        await call.message.edit_text("На эту дату нет свободных слотов.",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                         [InlineKeyboardButton(text="🔙 К выбору даты",
                                                               callback_data="back_tutor_reschedule_date")]
                                     ]))
        return
    buttons = [[InlineKeyboardButton(text=s, callback_data=f"t_reschedule_slot_{s}")] for s in slots]
    buttons.append([InlineKeyboardButton(text="🔙 К выбору даты", callback_data="back_tutor_reschedule_date")])
    await call.message.edit_text("Выберите новое время:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorRescheduleStates.waiting_time)


@dp.callback_query(F.data == "back_tutor_reschedule_date", StateFilter("*"))
async def back_tutor_reschedule_date(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tid = data["tutor_id"]
    dates = await get_available_dates(tid)
    buttons = [[InlineKeyboardButton(
        text=f"{d} ({WEEKDAY_NAMES[WEEKDAYS[datetime.strptime(d, '%d.%m.%Y').weekday()]]})",
        callback_data=f"t_reschedule_date_{d}")] for d in dates]
    buttons.append([InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_menu")])
    await call.message.edit_text("Выберите новую дату:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorRescheduleStates.waiting_date)


@dp.callback_query(F.data.startswith("t_reschedule_slot_"), StateFilter(TutorRescheduleStates.waiting_time))
async def tutor_reschedule_slot(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    slot = call.data.split("t_reschedule_slot_")[1]
    await state.update_data(new_time=slot)
    data = await state.get_data()
    text = (
        f"Перенос занятия:\n"
        f"Ученик: {data['student_username']}\n"
        f"Предмет: {data['subject']}\n"
        f"Старое: {data['old_date']} {data['old_time']}\n"
        f"Новое: {data['new_date']} {slot}\n\nПодтвердить перенос?"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_tutor_reschedule")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_tutor_reschedule_date")]
    ])
    await call.message.edit_text(text, reply_markup=keyboard)
    await state.set_state(TutorRescheduleStates.waiting_confirmation)


@dp.callback_query(F.data == "confirm_tutor_reschedule", StateFilter(TutorRescheduleStates.waiting_confirmation))
async def confirm_tutor_reschedule(call: CallbackQuery, state: FSMContext, bot: Bot):
    await safe_answer(call)
    data = await state.get_data()
    old_bid = data["old_booking_id"]
    tid = data["tutor_id"]
    new_date = data["new_date"]
    new_time = data["new_time"]
    subject = data["subject"]
    student_id = data["student_id"]
    student_username = data["student_username"]

    # --- 1. Удаляем старое сообщение ---
    old_booking = (await get_all_bookings()).get(old_bid)
    if old_booking and old_booking.get("channel_msg_id") and RECORDS_CHANNEL_ID:
        try:
            await bot.delete_message(chat_id=RECORDS_CHANNEL_ID, message_id=old_booking["channel_msg_id"])
        except Exception as e:
            logging.warning(f"Не удалось удалить старое сообщение: {e}")

    # --- 2. Отменяем старую запись ---
    await update_booking(old_bid, status="cancelled")

    # --- 3. Создаём новую запись и сразу подтверждаем ---
    new_id = await add_booking(tid, student_id, student_username, subject, new_date, new_time)
    await update_booking(new_id, status="confirmed", reminded=0)

    # --- 4. Отправляем новое сообщение в канал и сохраняем ID ---
    if RECORDS_CHANNEL_ID:
        tutors = await get_all_tutors()
        tutor = tutors.get(tid)
        tutor_name = tutor["name"] if tutor else "Неизвестный"
        record_msg = (
            f"✅ Подтверждена запись на занятие (перенос преподавателем)\n"
            f"👤 Ученик: {student_username} (ID: {student_id})\n"
            f"👨‍🏫 Преподаватель: {tutor_name}\n"
            f"📚 Предмет: {subject}\n"
            f"📅 Дата: {new_date} (МСК)\n"
            f"🕒 Время: {new_time} (МСК)"
        )
        try:
            sent_msg = await bot.send_message(chat_id=RECORDS_CHANNEL_ID, text=record_msg)
            await update_booking(new_id, channel_msg_id=sent_msg.message_id)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение в канал: {e}")

    # --- 5. Уведомления ---
    student_msg = (
        f"🔄 Преподаватель перенёс занятие.\n"
        f"Предмет: {subject}\n"
        f"Новое время: {new_date} {new_time} (МСК)"
    )
    await bot.send_message(student_id, student_msg)

    tutor_msg = f"✅ Вы перенесли занятие с {student_username} на {new_date} {new_time}."
    await bot.send_message(call.from_user.id, tutor_msg)

    # вместо:
    await call.message.edit_text("Перенос выполнен.")
    await call.message.answer("Главное меню:", reply_markup=await get_main_menu(call.from_user.id))

    # поставить:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К списку учеников", callback_data=f"tutor_students_{tid}")]
    ])
    await call.message.edit_text("Перенос выполнен.", reply_markup=keyboard)
    await state.clear()


# ==================== СТАТИСТИКА ПРЕПОДАВАТЕЛЯ ====================
@dp.callback_query(F.data.startswith("tutor_stats_"))
async def tutor_stats_menu(call: CallbackQuery):
    tid = int(call.data.split("_")[2])
    fin = await get_tutor_financials(tid)
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    comm_percent = tutor.get("commission_percent", 15) if tutor else 15
    text = (
        f"📊 Статистика за всё время\n"
        f"• Проведено занятий: {fin['total_lessons']}\n"
        f"• Общий доход: {fin['total_income']:.2f} руб.\n"
        f"• Комиссия ({comm_percent}%): {fin['commission_amount']:.2f} руб.\n"
        f"• Доход после комиссии: {fin['net_income']:.2f} руб.\n\n"
        "Выберите месяц для детализации:"
    )
    now = datetime.now()
    months = sorted(set((d.year, d.month) for d in [now - timedelta(days=30 * i) for i in range(12)]), reverse=True)
    buttons = [[InlineKeyboardButton(text=f"{y}-{m:02d}", callback_data=f"tutor_stats_month_{tid}_{y}_{m}")] for y, m in
               months]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_tutor_panel_{tid}")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("tutor_stats_month_"))
async def tutor_stats_month(call: CallbackQuery):
    parts = call.data.split("_")
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К общей статистике", callback_data=f"tutor_stats_{tid}")],
    ])
    await call.message.edit_text(text, reply_markup=keyboard)


# --- Настройка расписания ---
@dp.callback_query(F.data.startswith("tutor_schedule_"))
async def schedule_main(call: CallbackQuery, state: FSMContext):
    tid = int(call.data.split("_")[-1])
    await state.update_data(tid=tid)
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
    buttons = [[InlineKeyboardButton(text=f"✏️ {WEEKDAY_NAMES[day]}", callback_data=f"sched_day_{day}")] for day in WEEKDAYS]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_tutor_panel_{tid}")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.choose_day)



async def _show_day_management(call: CallbackQuery, state: FSMContext):
    """Вспомогательная функция для отображения управления конкретным днём."""
    data = await state.get_data()
    day = data["current_day"]
    tid = data["tid"]
    sched = await get_schedule(tid)
    slots = sched.get(day, [])
    blocked = await is_day_blocked(tid, day)

    if blocked:
        status_line = "🔒 День заблокирован (запись недоступна)\n"
    else:
        status_line = ""

    text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + status_line
    text += "\n".join(f"• {s}" for s in slots) if slots else "Нет слотов."

    buttons = [
        [InlineKeyboardButton(text="➕ Добавить слот", callback_data="add_slot")],
        [InlineKeyboardButton(text="📅 Заполнить промежуток", callback_data="add_range")],
    ]
    if slots:
        buttons.append([InlineKeyboardButton(text="❌ Удалить слот", callback_data="del_slot")])

    # Кнопка блокировки/разблокировки
    if blocked:
        buttons.append([InlineKeyboardButton(text="🔓 Разблокировать день", callback_data=f"unblock_day_{day}")])
    else:
        buttons.append([InlineKeyboardButton(text="🔒 Заблокировать день", callback_data=f"block_day_{day}")])

    buttons.append([InlineKeyboardButton(text="🔙 К дням недели", callback_data="back_to_schedule")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("sched_day_"), StateFilter(TutorScheduleStates.choose_day))
async def edit_day(call: CallbackQuery, state: FSMContext):
    day = call.data.split("_")[2]
    await state.update_data(current_day=day)
    await _show_day_management(call, state)


@dp.callback_query(F.data.startswith("block_day_"), StateFilter(TutorScheduleStates.manage_day_slots))
async def handle_block_day(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    day = call.data.split("block_day_")[1]
    data = await state.get_data()
    tid = data["tid"]
    await block_day(tid, day)
    # Перерисовываем управление днём
    await _show_day_management(call, state)


@dp.callback_query(F.data.startswith("unblock_day_"), StateFilter(TutorScheduleStates.manage_day_slots))
async def handle_unblock_day(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    day = call.data.split("unblock_day_")[1]
    data = await state.get_data()
    tid = data["tid"]
    await unblock_day(tid, day)
    await _show_day_management(call, state)


@dp.callback_query(F.data == "back_to_schedule", StateFilter(TutorScheduleStates.manage_day_slots))
async def back_to_schedule(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tid = data["tid"]
    sched = await get_schedule(tid)
    text = "Ваше расписание:\n"
    for day in WEEKDAYS:
        slots = sched.get(day, [])
        icon = "✅" if slots else ""
        text += f"{icon} {WEEKDAY_NAMES[day]}: {', '.join(slots) if slots else 'нет'}\n"
    buttons = [[InlineKeyboardButton(text=f"✏️ {WEEKDAY_NAMES[day]}", callback_data=f"sched_day_{day}")] for day in
               WEEKDAYS]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_tutor_panel_{tid}")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.choose_day)


@dp.callback_query(F.data == "add_slot", StateFilter(TutorScheduleStates.manage_day_slots))
async def add_slot_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    await call.message.edit_text("Введите временной слот в формате HH:MM-HH:MM, например 10:00-11:30:")
    await state.set_state(TutorScheduleStates.add_slot)


@dp.message(TutorScheduleStates.add_slot)
async def process_add_slot(message: Message, state: FSMContext):
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
    data = await state.get_data()
    tid = data["tid"]
    day = data["current_day"]
    sched = await get_schedule(tid)
    slots = sched.get(day, [])
    if slot in slots:
        await message.answer("Такой слот уже существует.")
        return
    await add_schedule_slot(tid, day, slot)
    await message.answer("Слот добавлен.")
    # Обновляем отображение
    slots = sched.get(day, []) + [slot]
    text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + "\n".join(f"• {s}" for s in slots)
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить слот", callback_data="add_slot")],
        [InlineKeyboardButton(text="📅 Заполнить промежуток", callback_data="add_range")],
        [InlineKeyboardButton(text="❌ Удалить слот", callback_data="del_slot")],
        [InlineKeyboardButton(text="🔙 К дням недели", callback_data="back_to_schedule")]
    ]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.manage_day_slots)


@dp.callback_query(F.data == "add_range", StateFilter(TutorScheduleStates.manage_day_slots))
async def add_range_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    # Клавиатура с выбором длительности
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="60 минут", callback_data="dur_60")],
        [InlineKeyboardButton(text="90 минут", callback_data="dur_90")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_schedule")]
    ])
    await call.message.edit_text("Выберите длительность занятия:", reply_markup=keyboard)
    await state.set_state(TutorScheduleStates.range_duration)


@dp.callback_query(F.data.startswith("dur_"), StateFilter(TutorScheduleStates.range_duration))
async def range_duration_chosen(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    duration = int(call.data.split("_")[1])  # 60 или 90
    await state.update_data(range_duration=duration)
    # Клавиатура с выбором перерыва
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Без перерыва", callback_data="brk_0")],
        [InlineKeyboardButton(text="10 минут", callback_data="brk_10")],
        [InlineKeyboardButton(text="15 минут", callback_data="brk_15")],
        [InlineKeyboardButton(text="20 минут", callback_data="brk_20")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="add_range_back")]
    ])
    await call.message.edit_text("Нужен ли перерыв между занятиями?", reply_markup=keyboard)
    await state.set_state(TutorScheduleStates.range_break)


@dp.callback_query(F.data == "add_range_back", StateFilter(TutorScheduleStates.range_break))
async def range_break_back(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    # Возвращаемся к выбору длительности
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="60 минут", callback_data="dur_60")],
        [InlineKeyboardButton(text="90 минут", callback_data="dur_90")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_schedule")]
    ])
    await call.message.edit_text("Выберите длительность занятия:", reply_markup=keyboard)
    await state.set_state(TutorScheduleStates.range_duration)


@dp.callback_query(F.data.startswith("brk_"), StateFilter(TutorScheduleStates.range_break))
async def range_break_chosen(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    break_min = int(call.data.split("_")[1])  # 0, 10, 15, 20
    await state.update_data(range_break=break_min)
    # Теперь запрашиваем промежуток времени
    await call.message.edit_text(
        "Введите промежуток времени в формате ЧЧ:ММ-ЧЧ:ММ (например, 09:00-15:30).\n"
        "Бот автоматически разобьёт его на слоты с учётом выбранной длительности и перерыва."
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
    start_time = clean_time_input(parts[0])
    end_time = clean_time_input(parts[1])
    for t in (start_time, end_time):
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError:
            await message.answer(f"Некорректное время «{t}». Пожалуйста, используйте формат ЧЧ:ММ (например, 09:00).")
            return

    # Получаем сохранённые параметры
    data = await state.get_data()
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
            existing.append(s)
            added += 1

    await message.answer(f"Добавлено {added} новых слотов (пропущены существующие).")
    # Показываем обновлённый список слотов
    display_text = f"Слоты для {WEEKDAY_NAMES[day]}:\n" + "\n".join(f"• {s}" for s in existing)
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить слот", callback_data="add_slot")],
        [InlineKeyboardButton(text="📅 Заполнить промежуток", callback_data="add_range")],
        [InlineKeyboardButton(text="❌ Удалить слот", callback_data="del_slot")],
        [InlineKeyboardButton(text="🔙 К дням недели", callback_data="back_to_schedule")]
    ]
    await message.answer(display_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.manage_day_slots)


@dp.callback_query(F.data == "del_slot", StateFilter(TutorScheduleStates.manage_day_slots))
async def del_slot_start(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    data = await state.get_data()
    tid = data["tid"]
    day = data["current_day"]
    sched = await get_schedule(tid)
    slots = sched.get(day, [])
    if not slots:
        await call.message.edit_text("Нет слотов для удаления.")
        return
    buttons = [[InlineKeyboardButton(text=s, callback_data=f"delslot_{s}")] for s in slots]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_schedule")])
    await call.message.edit_text("Выберите слот для удаления:",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(TutorScheduleStates.delete_slot)


@dp.callback_query(F.data.startswith("delslot_"), StateFilter(TutorScheduleStates.delete_slot))
async def confirm_del_slot(call: CallbackQuery, state: FSMContext):
    slot = call.data.split("_", 1)[1]
    data = await state.get_data()
    tid = data["tid"]
    day = data["current_day"]
    await delete_schedule_slot(tid, day, slot)
    await call.message.edit_text("Слот удалён.")
    sched = await get_schedule(tid)
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


# --- Подтверждение/отклонение заявок (уже использует БД) ---
@dp.callback_query(F.data.startswith("tutor_confirm_"))
async def tutor_confirm_booking(call: CallbackQuery, bot: Bot):
    await safe_answer(call)
    bid = int(call.data.split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking or booking["status"] != "pending":
        await call.message.edit_text("Заявка уже обработана.")
        return
    await update_booking(bid, status="confirmed", reminded=0)
    if RECORDS_CHANNEL_ID:
        tutors = await get_all_tutors()
        tutor = tutors.get(booking["tutor_id"])
        tutor_name = tutor["name"] if tutor else "Неизвестный"
        record_msg = (
            f"✅ Подтверждена запись на занятие\n"
            f"👤 Ученик: {booking['username']} (ID: {booking['user_id']})\n"
            f"👨‍🏫 Преподаватель: {tutor_name}\n"
            f"📚 Предмет: {booking['subject']}\n"
            f"📅 Дата: {booking['date']} (МСК)\n"
            f"🕒 Время: {booking['time_slot']} (МСК)"
        )
        try:
            sent_msg = await bot.send_message(chat_id=RECORDS_CHANNEL_ID, text=record_msg)
            # Сохраняем ID сообщения в БД
            await update_booking(bid, channel_msg_id=sent_msg.message_id)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение в канал: {e}")
    user_id = booking["user_id"]
    tutors = await get_all_tutors()
    tutor = tutors.get(booking["tutor_id"])
    tutor_name = tutor["name"] if tutor else "Неизвестный"
    await bot.send_message(user_id,
                           f"✅ Ваше занятие по предмету «{booking['subject']}» с преподавателем {tutor_name} на {booking['date']} (МСК) в {booking['time_slot']} (МСК) подтверждено!")
    tid = booking["tutor_id"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К списку учеников", callback_data=f"tutor_students_{tid}")]
    ])
    await call.message.edit_text("✅ Вы подтвердили занятие.", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("tutor_reject_"))
async def tutor_reject_booking(call: CallbackQuery, bot: Bot):
    await safe_answer(call)
    bid = int(call.data.split("_")[2])
    bookings = await get_all_bookings()
    booking = bookings.get(bid)
    if not booking or booking["status"] != "pending":
        await call.message.edit_text("Заявка уже обработана.")
        return
    user_id = booking["user_id"]
    await delete_booking(bid)
    await bot.send_message(user_id,
                           "❌ Ваша заявка на занятие была отклонена преподавателем. Вы можете записаться на другое время.")
    tid = booking["tutor_id"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К списку учеников", callback_data=f"tutor_students_{tid}")]
    ])
    await call.message.edit_text("❌ Заявка отклонена.", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("tutor_profile_"))
async def show_tutor_own_profile(call: CallbackQuery):
    await safe_answer(call)
    tid = int(call.data.split("_")[-1])
    user_tutor_id = await get_tutor_by_telegram_id(call.from_user.id)
    if user_tutor_id != tid:
        await call.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    tutors = await get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        await call.message.edit_text("Анкета не найдена.")
        return

    text = tutor["description"] + "\n\nПредметы и цены:\n"
    for subj, price in tutor["subjects"].items():
        text += f"• {subj} — {price} руб.\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в панель", callback_data=f"back_to_tutor_panel_{tid}")]
    ])

    if tutor["photo"]:
        await call.message.delete()
        await call.bot.send_photo(chat_id=call.message.chat.id, photo=tutor["photo"], caption=text,
                                  reply_markup=keyboard)
    else:
        await call.message.edit_text(text, reply_markup=keyboard)


# ==================== ПОМОЩЬ ====================
@dp.message(F.text.in_(["❓ Помощь"]))
async def help(message: types.Message):
    await message.answer("Открываю раздел помощи...", reply_markup=ReplyKeyboardRemove())
    help_text = (
        "📖 <b>Помощь по использованию бота</b>\n\n"
        "👤 <b>Для учеников</b>\n"
        "• <b>Информация о репетиторах</b> – узнайте об образовании, опыте, предметах и стоимости занятий каждого преподавателя.\n"
        "• <b>Информация о занятиях</b> – формат проведения (Zoom/Яндекс.Телемост), длительность (60 или 90 минут), "
        "действующие скидки и условия их суммирования.\n"
        "• <b>Запись на занятие</b> – выберите преподавателя, предмет, удобные дату и время из доступных слотов. "
        "Заявка уходит преподавателю, после подтверждения вы получите уведомление.\n"
        "• <b>Мои записи</b> – список ваших активных занятий. Здесь можно отменить или перенести запись "
        "(<b>не позднее чем за 24 часа</b> до начала), а также посмотреть статистику и остатки по абонементам.\n"
        "• <b>Оплата</b> – оплата по QR‑коду, банковской картой или переводом по СБП. "
        "Выберите удобный способ и следуйте инструкциям.\n"
        "• <b>Связь с преподавателем</b> – напишите сообщение конкретному преподавателю. "
        "Ответ придёт в этот же чат от имени бота.\n"
        "• <b>Поддержка</b> – задайте вопрос администратору, если возникли трудности.\n\n"
        "👨‍🏫 <b>Для преподавателей</b>\n"
        "• Доступ к панели появляется, если ваш Telegram ID добавлен в профиль репетитора.\n"
        "• <b>Мои ученики</b> – список всех активных записей к вам. Вы можете <b>подтвердить</b>, <b>отклонить</b>, "
        "<b>отменить</b> или <b>перенести</b> занятие (отмена/перенос доступны не позднее 24 часов до начала).\n"
        "• <b>Настроить расписание</b> – укажите рабочие дни и временные слоты (одиночные или целые промежутки). "
        "На основе расписания ученики видят свободные даты.\n"
        "• <b>Связь с учеником</b> – напишите ученику напрямую, выбрав его из списка ваших активных учеников.\n"
        "• <b>Статистика</b> – общая и помесячная информация о количестве занятий, доходе, комиссии и чистой прибыли.\n\n"
        "⏰ <b>Напоминания</b>\n"
        "За час до начала подтверждённого занятия и ученик, и преподаватель получают автоматическое напоминание.\n\n"
        "⚠️ <b>Важные правила</b>\n"
        "• Отмена и перенос занятия возможны <b>не позднее чем за 24 часа</b> до его начала.\n"
        "• Все записи, изменения и подтверждения сохраняются автоматически.\n"
        "• Для возврата в главное меню используйте кнопку <b>«Назад в меню»</b> или команду /start.\n"
        "• Если у вас нет доступа к нужному разделу, обратитесь в поддержку – администратор поможет с настройками."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer(help_text, reply_markup=keyboard)


# ==================== Очистка и напоминания ====================
async def cleanup_old_bookings():
    today = datetime.now().strftime("%d.%m.%Y")
    async with aiosqlite.connect("bot.db") as db:
        cursor = await db.execute(
            "UPDATE bookings SET status='completed' WHERE status IN ('pending','confirmed') AND date < ?",
            (today,)
        )
        if cursor.rowcount > 0:
            await db.commit()
            logging.info(f"Старые записи переведены в completed. Обновлено: {cursor.rowcount}")


async def periodic_cleanup():
    while True:
        await cleanup_old_bookings()
        await asyncio.sleep(3600)


async def send_reminders(bot: Bot):
    now = datetime.now()
    bookings = await get_all_bookings()
    for bid, b in bookings.items():
        if b.get("status") != "confirmed":
            continue
        if b.get("reminded"):
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
                f"с преподавателем {tutor_name}. Время: {b['date']} (МСК) {b['time_slot']}"
            )
            await bot.send_message(student_id, student_msg)

            tutor = tutors.get(tutor_id)
            if tutor and tutor.get("telegram_id"):
                tutor_msg = (
                    f"⏰ Напоминание! Через час у вас занятие по предмету «{b['subject']}» "
                    f"с учеником {b['username']} (ID: {student_id}). Время: {b['date']} (МСК) {b['time_slot']}"
                )
                try:
                    await bot.send_message(tutor["telegram_id"], tutor_msg)
                except:
                    pass

            await update_booking(bid, reminded=1)


async def send_pending_reminders(bot: Bot):
    """Отправляет преподавателям сводку неподтверждённых заявок."""
    bookings = await get_all_bookings()
    pending_by_tutor = {}
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for bid, b in bookings.items():
        if b["status"] != "pending":
            continue
        # Игнорируем заявки на прошедшие даты
        try:
            booking_date = datetime.strptime(b["date"], "%d.%m.%Y")
            if booking_date < today:
                continue
        except ValueError:
            continue

        pending_by_tutor.setdefault(b["tutor_id"], []).append(b)

    if not pending_by_tutor:
        return

    tutors = await get_all_tutors()
    for tid, plist in pending_by_tutor.items():
        tutor = tutors.get(tid)
        if not tutor or not tutor.get("telegram_id"):
            continue

        # Формируем сообщение
        lines = [f"🔔 У вас есть неподтверждённые заявки ({len(plist)}):"]
        for b in plist:
            lines.append(f"• {b['username']}: {b['subject']}, {b['date']} {b['time_slot']}")
        text = "\n".join(lines)

        # Кнопка быстрого перехода к списку учеников
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои ученики", callback_data=f"tutor_students_{tid}")]
        ])

        try:
            await bot.send_message(tutor["telegram_id"], text, reply_markup=keyboard)
        except Exception as e:
            logging.warning(f"Не удалось отправить напоминание преподавателю {tid}: {e}")

async def pending_reminder_loop(bot: Bot):
    """Проверяет время и отправляет напоминания в 9, 15 и 21 час по Москве."""
    msk = timezone(timedelta(hours=3))
    while True:
        now = datetime.now(msk)
        if now.hour in (9, 15, 21) and now.minute == 0:
            await send_pending_reminders(bot)
        await asyncio.sleep(60)  # проверка каждую минуту

async def reminder_loop(bot: Bot):
    while True:
        await send_reminders(bot)
        await asyncio.sleep(60)


# ==================== ЗАПУСК ====================
async def main() -> None:
    await init_db()
    await migrate_database()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(periodic_cleanup())
    asyncio.create_task(reminder_loop(bot))
    asyncio.create_task(pending_reminder_loop(bot))
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
