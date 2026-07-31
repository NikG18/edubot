import asyncio
import logging
import sys
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

ADMING_ID = 846400165
ADMINJ_ID = 5116346967
# 1. Токен бота (получите у @BotFather в Telegram)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Передайте его через export BOT_TOKEN=...")


# Инициализируем диспетчер
dp = Dispatcher()

# -------------------- FSM состояния --------------------
class BookingStates(StatesGroup):
    choosing_tutor = State()
    choosing_subject = State()
    waiting_date_time = State()
    waiting_confirmation = State()

# -------------------- ГЛАВНОЕ МЕНЮ (Reply) --------------------
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="ℹ️ Информация о репетиторах")],
        [KeyboardButton(text="📚 Информация о занятиях")],
        [KeyboardButton(text="📝 Запись на занятие")],
        [KeyboardButton(text="💳 Оплата")],
        [KeyboardButton(text="📖 Учебные материалы")],
        [KeyboardButton(text="✉️ Связь с преподавателем")],
        [KeyboardButton(text="❓ Помощь")]
    ],
    resize_keyboard=True
)

# -------------------- ХЭНДЛЕРЫ --------------------

@dp.message(Command("start"))
async def Start(message: Message) -> None:
    await message.answer(
        f"Привет, {html.bold(message.from_user.full_name)}! Я онлайн ассистент Никиты Тимуровича. Чем могу помочь?",
        reply_markup=main_menu
    )

# Обработчик для кнопки "Назад" (если используется где-то)
@dp.message(F.text.in_(["🔙 Назад"]))
async def main_menu_buttons(message: Message) -> None:
    await message.answer(
        f"{html.bold(message.from_user.full_name)}, Чем могу помочь?",
        reply_markup=main_menu
    )

# -------------------- ИНФОРМАЦИЯ О РЕПЕТИТОРАХ --------------------
@dp.message(F.text.in_(["ℹ️ Информация о репетиторах"]))
async def repet(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍🏫 Никита Тимурович", callback_data="tutor_nikita")],
        [InlineKeyboardButton(text="👩‍🏫 Юлия Евгеньевна", callback_data="tutor_julia")],
        [InlineKeyboardButton(text="👨‍🏫 Никита Дмитриевич", callback_data="tutor_nikitak")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_tutors")
async def back_to_tutors(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍🏫 Никита Тимурович", callback_data="tutor_nikita")],
        [InlineKeyboardButton(text="👩‍🏫 Юлия Евгеньевна", callback_data="tutor_julia")],
        [InlineKeyboardButton(text="👨‍🏫 Никита Дмитриевич", callback_data="tutor_nikitak")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    await call.answer()

@dp.callback_query(F.data == "tutor_nikita")
async def show_nikita_info(call: CallbackQuery):
    await call.message.edit_text(
        "Приветствую, меня зовут Никита Тимурович Ганжа кратко расскажу о себе. Учусь в РНИМУ им. Пирогова на 4 курсе, в основном на отлично (на одной сессии была четверка). Опыт преподавания имеется, вел курсы по химии в школе. Химией занимаюсь с 8 класса, два раза был призером регионального этапа по химии, 99 баллов на ЕГЭ. Хорошо разбираюсь в физике, биологии, математике и истории. "
        "Работаю с учениками на фундаментальное понимание химии, а не «тут нужно просто выучить». Объясняю на примерах из жизни, из человеческого организма и природы."
        "Если вам кажется, что вы совсем не знаете химию, то я изменю это уже к 4 занятию. Первое пробное занятие 30 минут, бесплатно.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_tutors")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "tutor_julia")
async def show_julia_info(call: CallbackQuery):
    await call.message.edit_text(
        "Приветствую, меня зовут Юлия Евгеньевна Паймурзова...\n\n"
        "Хотите записаться на пробное занятие?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_tutors")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "tutor_nikitak")
async def show_kolebaev_info(call: CallbackQuery):
    await call.message.edit_text(
        "Приветствую! Меня зовут Колебаев Никита Дмитриевич, я студент 4-ого курса МФТИ. Обучаюсь на бюджете по программе «Геокосмические науки и технологии». 
        "Имею опыт преподавания физики и математики для школьников: личное репетиторство и ЗФТШ (заочная физико-техническая школа при МФТИ). 
        "Окончил школу с золотой медалью, имею фундаментальные знания в области математики, физики и информатики (в том числе программирование на языках Python/C++). 
        "ЕГЭ сдал в 2023 году на следующие баллы: физика – 95, информатика – 90. Со средней школы увлекаюсь робототехникой и ракетостроением. 
        "По этим направлениям участвовал в соревнованиях всероссийского масштаба, в которых занимал в том числе призерские места. В институте продолжаю развиваться в данных направлениях и побеждать в вузовских состязаниях. 
        "Умею доступно спокойно и основательно объяснить материал любой сложности. Если испытываете трудности в понимании физики, математики и информатики или у Вас есть желание лучше прокачаться в этих дисциплинах, с радостью готов помочь Вам! 
        "Первое пробное занятие 30 минут бесплатно."
        "Хотите записаться на пробное занятие?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_tutors")]
        ])
    )
    await call.answer()

# -------------------- ИНФОРМАЦИЯ О ЗАНЯТИЯХ --------------------
@dp.message(F.text.in_(["📚 Информация о занятиях"]))
async def lesson_info(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Химия", callback_data="himiy")],
        [InlineKeyboardButton(text="⚛️ Физика", callback_data="fizika")],
        [InlineKeyboardButton(text="➗ Математика", callback_data="matem")],
        [InlineKeyboardButton(text="💻 Информатика", callback_data="inform")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Какой предмет Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_sj")
async def back_to_sj(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Химия", callback_data="himiy")],
        [InlineKeyboardButton(text="⚛️ Физика", callback_data="fizika")],
        [InlineKeyboardButton(text="➗ Математика", callback_data="matem")],
        [InlineKeyboardButton(text="💻 Информатика", callback_data="inform")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Какой предмет Вас интересует?", reply_markup=keyboard)
    await call.answer()

@dp.callback_query(F.data == "himiy")
async def himiy(call: CallbackQuery):
    await call.message.edit_text(
        "🧪 Химию преподает Никита Тимурович и Юлия Евгеньевна, хотели бы узнать цену?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Цена занятий", callback_data="priceh")],
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "fizika")
async def fizika(call: CallbackQuery):
    await call.message.edit_text(
        "⚛️ Физику преподает Никита Дмитриевич и Никита Тимурович, хотели бы узнать цену?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Цена занятий", callback_data="pricef")],
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "matem")
async def matem(call: CallbackQuery):
    await call.message.edit_text(
        "➗ Математику преподает пока только Никита Дмитриевич, хотели бы узнать цену?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Цена занятий", callback_data="pricem")],
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "inform")
async def inform(call: CallbackQuery):
    await call.message.edit_text(
        "💻 Информатику преподает пока только Никита Дмитриевич, хотели бы узнать цену?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Цена занятий", callback_data="pricei")],
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "priceh")
async def priceh(call: CallbackQuery):
    await call.message.edit_text(
        "💰 Цена за 1 час индивидуального занятия:\n"
        "Никита Тимурович → 2500 руб.\n"
        "Юлия Евгеньевна → 1500 руб.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "pricef")
async def pricef(call: CallbackQuery):
    await call.message.edit_text(
        "💰 Цена за 1 час индивидуального занятия:\n"
        "Никита Тимурович → 1500 руб.\n"
        "Никита Дмитриевич → 2500 руб.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "pricem")
async def pricem(call: CallbackQuery):
    await call.message.edit_text(
        "💰 Цена за 1 час индивидуального занятия:\n"
        "Никита Дмитриевич → 2500 руб.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "pricei")
async def pricei(call: CallbackQuery):
    await call.message.edit_text(
        "💰 Цена за 1 час индивидуального занятия:\n"
        "Никита Дмитриевич → 2500 руб.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

# -------------------- ЗАПИСЬ НА ЗАНЯТИЕ --------------------
@dp.message(F.text.in_(["📝 Запись на занятие"]))
async def zapis(message: types.Message, state: FSMContext):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍🏫 Никита Тимурович", callback_data="tutor_nikitaz")],
        [InlineKeyboardButton(text="👩‍🏫 Юлия Евгеньевна", callback_data="tutor_juliaz")],
        [InlineKeyboardButton(text="👨‍🏫 Никита Дмитриевич", callback_data="tutor_nikitakz")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    await state.clear()

@dp.callback_query(F.data.startswith("tutor_"))
async def choose_tutor(call: CallbackQuery, state: FSMContext):
    await call.answer()
    tutor_map = {
        "tutor_nikitaz": "Никита Тимурович",
        "tutor_juliaz": "Юлия Евгеньевна",
        "tutor_nikitakz": "Никита Дмитриевич"
    }
    tutor_name = tutor_map.get(call.data)
    if not tutor_name:
        await call.message.edit_text("Ошибка выбора репетитора.")
        return
    await state.update_data(tutor=tutor_name)

    if call.data == "tutor_nikitaz":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧪 Химия", callback_data="himiyz")],
            [InlineKeyboardButton(text="⚛️ Физика", callback_data="fizikaz")],
            [InlineKeyboardButton(text="🔙 Назад к репетиторам", callback_data="back_to_menuz")]
        ])
    elif call.data == "tutor_juliaz":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧪 Химия", callback_data="himiyz")],
            [InlineKeyboardButton(text="🔙 Назад к репетиторам", callback_data="back_to_menuz")]
        ])
    elif call.data == "tutor_nikitakz":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧪 Химия", callback_data="himiyz")],
            [InlineKeyboardButton(text="⚛️ Физика", callback_data="fizikaz")],
            [InlineKeyboardButton(text="➗ Математика", callback_data="matemz")],
            [InlineKeyboardButton(text="💻 Информатика", callback_data="informz")],
            [InlineKeyboardButton(text="🔙 Назад к репетиторам", callback_data="back_to_menuz")]
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к репетиторам", callback_data="back_to_menuz")]
        ])

    await call.message.edit_text("На занятие по какому предмету вы хотите записаться?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_menuz")
async def back_to_menuz(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍🏫 Никита Тимурович", callback_data="tutor_nikitaz")],
        [InlineKeyboardButton(text="👩‍🏫 Юлия Евгеньевна", callback_data="tutor_juliaz")],
        [InlineKeyboardButton(text="👨‍🏫 Никита Дмитриевич", callback_data="tutor_nikitakz")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Кто из репетиторов Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data == "himiyz")
async def subject_himiyz(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.update_data(subject="Химия")
    await call.message.edit_text(
        "Вы выбрали предмет: 🧪 Химия.\n"
        "Теперь введите желаемую дату и время занятия в формате:\n"
        "ДД.ММ.ГГГГ ЧЧ:MM\n"
        "Например: 15.08.2026 14:30"
    )
    await state.set_state(BookingStates.waiting_date_time)

@dp.callback_query(F.data == "fizikaz")
async def subject_fizikaz(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.update_data(subject="Физика")
    await call.message.edit_text(
        "Вы выбрали предмет: ⚛️ Физика.\n"
        "Теперь введите желаемую дату и время занятия в формате:\n"
        "ДД.ММ.ГГГГ ЧЧ:MM\n"
        "Например: 15.08.2026 14:30"
    )
    await state.set_state(BookingStates.waiting_date_time)

@dp.callback_query(F.data == "matemz")
async def subject_matemz(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.update_data(subject="Математика")
    await call.message.edit_text(
        "Вы выбрали предмет: ➗ Математика.\n"
        "Теперь введите желаемую дату и время занятия в формате:\n"
        "ДД.ММ.ГГГГ ЧЧ:MM\n"
        "Например: 15.08.2026 14:30"
    )
    await state.set_state(BookingStates.waiting_date_time)

@dp.callback_query(F.data == "informz")
async def subject_informz(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.update_data(subject="Информатика")
    await call.message.edit_text(
        "Вы выбрали предмет: 💻 Информатика.\n"
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
        await call.message.answer("Вы записаны на занятие. Ожидайте подтверждения от преподавателя.", reply_markup=main_menu)
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
    await call.message.answer("Главное меню:", reply_markup=main_menu)

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

# (Обработчики для bookh, bookf, videh, videf не добавлены – при необходимости допишите аналогично)

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

# -------------------- ГЛОБАЛЬНАЯ КНОПКА НАЗАД В МЕНЮ --------------------
@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await call.message.delete()
    await call.message.answer("Главное меню:", reply_markup=main_menu)

# -------------------- ЗАПУСК --------------------
async def main() -> None:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
