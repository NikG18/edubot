import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, html, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import CallbackQuery
import os
# 1. Токен бота (получите у @BotFather в Telegram)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Передайте его через export BOT_TOKEN=...")

# Инициализируем диспетчер (главный роутер для хэндлеров)
dp = Dispatcher()


# 2. Хэндлер на команду /start
@dp.message(Command("start"))
async def Start(message: Message) -> None:
    """Хэндлер срабатывает, когда пользователь отправляет команду /start"""
    # Отправляем приветствие с форматированием (html.bold делает текст жирным)
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Информация о репетиторах")],
        [KeyboardButton(text="Информация о занятиях")],
        [KeyboardButton(text="Запись на занятие")],
        [KeyboardButton(text="Оплата")],
        [KeyboardButton(text="Учебные материалы")],
        [KeyboardButton(text="Связь с преподавателем")],
        [KeyboardButton(text="Помощь")]], resize_keyboard=True)
    await message.answer(
        f"Привет, {html.bold(message.from_user.full_name)}! Я онлайн ассистент Никиты Тимуровича. Чем могу помочь?", reply_markup=keyboard)

#@dp.message()
#async def handle_message(message: types.Message):
#    # ID пользователя, который отправил это сообщение                     УЗНАТЬ ID
#   user_id = message.from_user.id
 #   await message.answer(f"Ваш ID: {user_id}")

@dp.message(F.text.in_(["Назад"]))
async def main_menu_buttons(message: Message) -> None:
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Информация о репетиторах")],
        [KeyboardButton(text="Информация о занятиях")],
        [KeyboardButton(text="Запись на занятие")],
        [KeyboardButton(text="Оплата")],
        [KeyboardButton(text="Учебные материалы")],
        [KeyboardButton(text="Связь с преподавателем")],
        [KeyboardButton(text="Помощь")]], resize_keyboard=True)
    await message.answer(
        f" {html.bold(message.from_user.full_name)}, Чем могу помочь?", reply_markup=keyboard)

#@dp.message(F.text.in_([ "Информация о репетиторах"]))
#async def repet_info(message: types.Message):
#    await message.answer(reply_markup=ReplyKeyboardRemove())

#@dp.callback_query(F.data == "tutor_julia")
#async def show_julia_info(call: CallbackQuery):
 #   await call.message.edit_text("Приветствую, меня зовут Юлия Евгеньевна Паймурзова...")
 #   await call.answer()
#######
@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(call: CallbackQuery):
    await call.message.delete()
     #Можно удалить сообщение или вернуть главное меню
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Информация о репетиторах")],
        [KeyboardButton(text="Информация о занятиях")],
        [KeyboardButton(text="Запись на занятие")],
        [KeyboardButton(text="Оплата")],
        [KeyboardButton(text="Учебные материалы")],
        [KeyboardButton(text="Связь с преподавателем")],
        [KeyboardButton(text="Помощь")]], resize_keyboard=True)
    await call.message.answer("Вы вернулись в главное меню.", reply_markup=keyboard)
    await call.answer()

@dp.callback_query(F.data == "back_to_tutors")
async def back_to_tutors(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Никита Тимурович", callback_data="tutor_nikita")],
        [InlineKeyboardButton(text="Юлия Евгеньевна", callback_data="tutor_julia")],
        [InlineKeyboardButton(text="Никита Дмитриевич", callback_data="tutor_nikitak")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Кто из репетиторов Вас интересует?", reply_markup=keyboard)
    await call.answer()



@dp.message(F.text.in_(["Информация о репетиторах"]))
async def repet(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
                # Создаём инлайн-кнопки для каждого репетитора
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Никита Тимурович", callback_data="tutor_nikita")],
        [InlineKeyboardButton(text="Юлия Евгеньевна", callback_data="tutor_julia")],
        [InlineKeyboardButton(text="Никита Дмитриевич", callback_data="tutor_nikitak")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data == "tutor_nikita")
async def show_nikita_info(call: CallbackQuery):
    await call.message.edit_text(
        "Приветствую, меня зовут Никита Тимурович Ганжа кратко расскажу о себе. Учусь в РНИМУ им. Пирогова на 4 курсе, в основном на отлично (на одной сессии была четверка). Опыт преподавания имеется, вел курсы по химии в школе. Химией занимаюсь с 8 класса, два раза был призером регионального этапа по химии, 99 баллов на ЕГЭ. Хорошо разбираюсь в физике, биологии, математике и истории. "
        "Работаю с учениками на фундаментальное понимание химии, а не «тут нужно просто выучить». Объясняю на примерах из жизни, из человеческого организма и природы."
        "Если вам кажется, что вы совсем не знаете химию, то я изменю это уже к 4 занятию. Первое пробное занятие 30 минут, бесплатно.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        #    [InlineKeyboardButton(text="Записаться", callback_data="signup_nikita")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_tutors")]
        ])
    )
    await call.answer()  # обязательно отвечаем на callback
@dp.callback_query(F.data == "tutor_julia")
async def show_julia_info(call: CallbackQuery):
    await call.message.edit_text(
        "Приветствую, меня зовут Юлия Евгеньевна Паймурзова...\n\n"
        "Хотите записаться на пробное занятие?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        #    [InlineKeyboardButton(text="Записаться", callback_data="signup_julia")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_tutors")]
        ])
    )
    await call.answer()  # обязательно отвечаем на callback

@dp.callback_query(F.data == "tutor_nikitak")
async def show_kolebaev_info(call: CallbackQuery):
    await call.message.edit_text(
        "Приветствую, меня зовут Никита Дмитриевич Колебаев...\n\n"
        "Хотите записаться на пробное занятие?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        #    [InlineKeyboardButton(text="Записаться", callback_data="signup_julia")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_tutors")]
        ])
    )
    await call.answer()  # обязательно отвечаем на callback


#        await message.answer("Приветствую, меня зовут Никита Тимурович Ганжа кратко расскажу о себе. Учусь в РНИМУ им. Пирогова на 4 курсе, в основном на отлично (на одной сессии была четверка). Опыт преподавания имеется, вел курсы по химии в школе. Химией занимаюсь с 8 класса, два раза был призером регионального этапа по химии, 99 баллов на ЕГЭ. Хорошо разбираюсь в физике, биологии, математике и истории. "
#                             "Работаю с учениками на фундаментальное понимание химии, а не «тут нужно просто выучить». Объясняю на примерах из жизни, из человеческого организма и природы."
#      Никита Дмитриевич Колебаев                       "Если вам кажется, что вы совсем не знаете химию, то я изменю это уже к 4 занятию. Первое пробное занятие 30 минут, бесплатно.")
#####
@dp.message(F.text.in_(["Информация о занятиях"]))
async def lesson_info(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
                # Создаём инлайн-кнопки для каждого репетитора
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Химия", callback_data="himiy")],
        [InlineKeyboardButton(text="Физика", callback_data="fizika")],
        [InlineKeyboardButton(text="Математика", callback_data="matem")],
        [InlineKeyboardButton(text="Информатика", callback_data="inform")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Какой предмет Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_sj")
async def back_to_sj(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Химия", callback_data="himiy")],
        [InlineKeyboardButton(text="Физика", callback_data="fizika")],
        [InlineKeyboardButton(text="Математика", callback_data="matem")],
        [InlineKeyboardButton(text="Информатика", callback_data="inform")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Какой предмет Вас интересует?", reply_markup=keyboard)
    await call.answer()


@dp.callback_query(F.data == "himiy")
async def himiy(call: CallbackQuery):
    await call.message.edit_text(
        "Химию преподает Никита Тимурович и Юлия Евгеньевна, хотели бы узнать цену?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Цена занятий", callback_data="priceh")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()  # обязательно отвечаем на callback
@dp.callback_query(F.data == "fizika")
async def fizika(call: CallbackQuery):
    await call.message.edit_text(
        "Физику преподает Никита Дмитриевич и Никита Тимурович, хотели бы узнать цену?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Цена занятий", callback_data="pricef")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()
@dp.callback_query(F.data == "matem")
async def matem(call: CallbackQuery):
    await call.message.edit_text(
        "Математику преподает пока только Никита Дмитриевич, хотели бы узнать цену?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Цена занятий", callback_data="pricem")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()  # обязательно отвечаем на callback

@dp.callback_query(F.data == "inform")
async def inform(call: CallbackQuery):
    await call.message.edit_text(
        "Информатику преподает пока только Никита Дмитриевич, хотели бы узнать цену?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Цена занятий", callback_data="pricei")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()  # обязательно отвечаем на callback

@dp.callback_query(F.data == "priceh")
async def priceh(call: CallbackQuery):
    await call.message.edit_text(
        "Цена за 1 час индивидуального занятия:"
        "Никита Тимурович --> 2500 рублей"
        "Юлия Евгеньевна --> 1500 рублей",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "pricef")
async def pricef(call: CallbackQuery):
    await call.message.edit_text(
        "Цена за 1 час индивидуального занятия:"
        "Никита Тимурович --> 1500 рублей"
        "Никита Дмитриевич --> 2500 рублей",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "pricem")
async def pricem(call: CallbackQuery):
    await call.message.edit_text(
        "Цена за 1 час индивидуального занятия:"
        "Никита Дмитриевич --> 2500 рублей",

        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "pricei")
async def pricei(call: CallbackQuery):
    await call.message.edit_text(
        "Цена за 1 час индивидуального занятия:"
        "Никита Дмитриевич --> 2500 рублей",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()

#####################################################################
@dp.message(F.text.in_(["Запись на занятие"]))
async def zapis(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
                # Создаём инлайн-кнопки для каждого репетитора
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Никита Тимурович", callback_data="tutor_nikitaz")],
        [InlineKeyboardButton(text="Юлия Евгеньевна", callback_data="tutor_juliaz")],
        [InlineKeyboardButton(text="Никита Дмитриевич", callback_data="tutor_nikitakz")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Кто из репетиторов Вас интересует?", reply_markup=keyboard)

@dp.callback_query(F.data == "pred")
async def pred(call: CallbackQuery):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
                # Создаём инлайн-кнопки для каждого репетитора
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Химия", callback_data="himiyz")],
        [InlineKeyboardButton(text="Физика", callback_data="fizikaz")],
        [InlineKeyboardButton(text="Математика", callback_data="matemz")],
        [InlineKeyboardButton(text="Информатика", callback_data="informz")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menuz")]
    ])
    await message.answer("На занятие по какому предмету вы хотите записаться?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_sj")
async def back_to_sj(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Химия", callback_data="himiy")],
        [InlineKeyboardButton(text="Физика", callback_data="fizika")],
        [InlineKeyboardButton(text="Математика", callback_data="matem")],
        [InlineKeyboardButton(text="Информатика", callback_data="inform")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Какой предмет Вас интересует?", reply_markup=keyboard)
    await call.answer()


@dp.callback_query(F.data == "himiy")
async def himiy(call: CallbackQuery):
    await call.message.edit_text(
        "Химию преподает Никита Тимурович и Юлия Евгеньевна, хотели бы узнать цену?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Цена занятий", callback_data="priceh")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()  # обязательно отвечаем на callback
@dp.callback_query(F.data == "fizika")
async def fizika(call: CallbackQuery):
    await call.message.edit_text(
        "Физику преподает Никита Дмитриевич и Никита Тимурович, хотели бы узнать цену?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Цена занятий", callback_data="pricef")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_sj")]
        ])
    )
    await call.answer()




#######################################################################################


@dp.message(F.text.in_(["Оплата"]))
async def oplata(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
                # Создаём инлайн-кнопки
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплата по QR-коду", callback_data="qr")],
        [InlineKeyboardButton(text="Оплата банковской картой", callback_data="card")],
        [InlineKeyboardButton(text="Перевод СБП по номеру телефона", callback_data="sbp")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Какой способ оплаты вам удобнее?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_pay")
async def back_to_pay(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплата по QR-коду", callback_data="qr")],
        [InlineKeyboardButton(text="Оплата банковской картой", callback_data="card")],
        [InlineKeyboardButton(text="Перевод СБП по номеру телефона", callback_data="sbp")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Какой способ оплаты вам удобнее?", reply_markup=keyboard)
    await call.answer()


@dp.callback_query(F.data == "qr")
async def qr(call: CallbackQuery):
    await call.message.edit_text(
        "Сканируйте QR-код для оплаты в приложении вашего банка",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_pay")]
        ])
    )
    await call.answer()  # обязательно отвечаем на callback
@dp.callback_query(F.data == "card")
async def card(call: CallbackQuery):
    await call.message.edit_text(
        "Переходите по ссылке и следуйте дальнейшим инструкциям",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Цена занятий", callback_data="pricef")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_pay")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "sbp")
async def sbp(call: CallbackQuery):
    await call.message.edit_text(
        "Перевод выполняйте указывая предмет и дату занятия по номеру 89035370929 на Т-банк",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_pay")]
        ])
    )
    await call.answer()


#####################################################################################################

@dp.message(F.text.in_(["Учебные материалы"]))
async def material(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
                # Создаём инлайн-кнопки для каждого репетитора
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Учебные пособия", callback_data="book")],
        [InlineKeyboardButton(text="Авторские видео", callback_data="vid")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
    ])
    await message.answer("Вы ищете пособия или видео?", reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_mat")
async def back_to_mat(call: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Учебные пособия", callback_data="book")],
        [InlineKeyboardButton(text="Авторские видео", callback_data="vid")],
        [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
    ])
    await call.message.edit_text("Вы ищете пособия или видео?", reply_markup=keyboard)
    await call.answer()


@dp.callback_query(F.data == "book")
async def book(call: CallbackQuery):
    await call.message.edit_text(
        "Учебники и таблиицы",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Химия", callback_data="bookh")],
            [InlineKeyboardButton(text="Физика", callback_data="bookf")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_mat")]
        ])
    )
    await call.answer()  # обязательно отвечаем на callback
@dp.callback_query(F.data == "vid")
async def vid(call: CallbackQuery):
    await call.message.edit_text(
        "Видеоматериалы(записи реакций и явлений)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Химия", callback_data="videh")],
            [InlineKeyboardButton(text="Физика", callback_data="videf")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="back_to_mat")]
        ])
    )
    await call.answer()

##################################################################################################
@dp.message(F.text.in_(["Связь с преподавателем"]))
async def svyaz(message: types.Message):
    await message.answer("Переходим в раздел...", reply_markup=ReplyKeyboardRemove())
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отправить", callback_data="otprav")],
                         [InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]
                         ])
    await message.answer(
        "Напишите, что вы хотите сообщить преподавателю, нажмите кнопку Отправить, после чего ожидайте ответа.",
        reply_markup=keyboard)





######################################################################################################
@dp.message(F.text.in_(["Помощь"]))
async def help(message: types.Message):
    await message.answer("Сообщаю Вам информацию о каждом разделе...", reply_markup=ReplyKeyboardRemove())
    # Создаём инлайн-кнопки
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu")]])
    await message.answer("В разделе Информация о репетиторах вы можете узнать об опыте и образовании каждого из преподавателей. "
                         "Информация о занятиях включает в себя прайслист каждого из преподавателей)",
                         reply_markup=keyboard)




# 4. Главная функция запуска бота
async def main() -> None:
    # Настраиваем свойства бота по умолчанию (включая HTML-разметку для текста)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Запускаем опрос серверов Telegram (Long Polling)
    # drop_pending_updates=True удаляет сообщения, пришедшие боту, пока он был выключен
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    # Настраиваем вывод логов в консоль, чтобы видеть работу бота и ошибки
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)


    # Запускаем асинхронный цикл
    asyncio.run(main())
