import database as _db
import Bot_test_legacy as legacy

from legal_common import (
    DOCS,
    STUDENT_DOC_TYPES,
    TUTOR_DOC_TYPES,
    install_payment_acceptance_hook,
    install_subscription_acceptance_hook,
    record_student_docs_presented,
    record_student_privacy_continued,
    record_student_privacy_presented,
    student_privacy_notice_completed,
)


def _url_keyboard(doc_types):
    rows = []
    for doc_type in doc_types:
        doc = DOCS[doc_type]
        rows.append([
            legacy.InlineKeyboardButton(text=f"📄 {doc['title']}", url=doc["url"])
        ])
    rows.append([
        legacy.InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu")
    ])
    return legacy.InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_student_legal_before_payment(user_id: int, booking_id=None):
    await record_student_docs_presented(user_id, "telegram", booking_id)
    keyboard = _url_keyboard(("student_offer", "privacy_policy"))
    await legacy.send_to_user(
        user_id,
        "telegram",
        "Перед оплатой ознакомьтесь с публичной офертой и Политикой обработки персональных данных. "
        "Оплата конкретного заказа является акцептом оферты в редакции, указанной в документах.",
        reply_markup_tg=keyboard.model_dump_json(),
    )


async def _show_privacy_notice(message, context: str, continue_callback: str):
    user_id = message.from_user.id
    await record_student_privacy_presented(user_id, "telegram", context)
    keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
        [legacy.InlineKeyboardButton(
            text="📄 Политика обработки ПД",
            url=DOCS["privacy_policy"]["url"],
        )],
        [legacy.InlineKeyboardButton(
            text="▶️ Продолжить",
            callback_data=continue_callback,
        )],
        [legacy.InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu")],
    ])
    await message.answer(
        "До начала записи ознакомьтесь с Политикой обработки персональных данных. "
        "Для оформления записи Сервис использует данные, необходимые для бронирования и исполнения заказа, "
        "включая ваш Telegram ID/username и сведения о выбранном занятии.\n\n"
        "Нажмите «Продолжить», чтобы перейти к записи.",
        reply_markup=keyboard,
    )


_original_get_main_menu = legacy.get_main_menu


async def _legal_get_main_menu(user_id: int):
    keyboard = await _original_get_main_menu(user_id)
    if not any(
        button.text == "📄 Документы"
        for row in keyboard.keyboard
        for button in row
    ):
        keyboard.keyboard.append([legacy.KeyboardButton(text="📄 Документы")])
    return keyboard


legacy.get_main_menu = _legal_get_main_menu


@legacy.dp.message(legacy.F.text == "📄 Документы")
async def legal_documents_menu(message):
    tutor_id = await legacy.get_tutor_by_telegram_id(message.from_user.id)
    is_tutor = tutor_id is not None and message.from_user.id != legacy.ADMING_ID
    doc_types = TUTOR_DOC_TYPES if is_tutor else STUDENT_DOC_TYPES
    intro = (
        "📄 Документы репетитора\n\n"
        "Первичный акцепт агентского договора и отдельные согласия репетитора оформляются "
        "через Яндекс Форму. Здесь всегда доступны актуальные редакции."
        if is_tutor else
        "📄 Юридические документы\n\n"
        "Здесь доступны актуальные документы для пользователя Сервиса."
    )
    await message.answer(intro, reply_markup=_url_keyboard(doc_types))


# Обычная запись: до первого сбора данных показываем текущую Политику ПД.
_original_zapis = legacy.zapis


async def _legal_zapis(message, state):
    if not await student_privacy_notice_completed(message.from_user.id, "telegram"):
        await state.clear()
        await message.answer("Переходим в раздел...", reply_markup=legacy.ReplyKeyboardRemove())
        await _show_privacy_notice(message, "regular_booking", "legal_continue_regular_booking")
        return
    return await _original_zapis(message, state)


legacy.zapis.__code__ = _legal_zapis.__code__


@legacy.dp.callback_query(legacy.F.data == "legal_continue_regular_booking")
async def legal_continue_regular_booking(call, state):
    await legacy.safe_answer(call)
    await record_student_privacy_continued(call.from_user.id, "telegram", "regular_booking")
    await state.clear()
    keyboard = await legacy.make_tutors_keyboard("tutor_booking", back_callback="back_to_menu")
    await call.message.edit_text("Кто из репетиторов Вас интересует?", reply_markup=keyboard)


# Пробное занятие запускается из карточки репетитора, поэтому ставим такой же gate
# прямо перед началом trial-flow.
_original_start_trials_booking = legacy.start_trials_booking


async def _start_trial_after_privacy(call, state):
    tid = int(call.data.split("_")[1])
    tutors = await legacy.get_all_tutors()
    tutor = tutors.get(tid)
    if not tutor:
        if call.message.content_type != "text":
            await call.message.delete()
        await call.message.answer("Репетитор не найден.")
        return

    await state.update_data(tutor_id=tid, tutor_name=tutor["name"])
    subjects = list(tutor["subjects"].keys())

    if len(subjects) == 1:
        subject = subjects[0]
        await state.update_data(subject=subject)
        if call.message.content_type != "text":
            await call.message.delete()
            keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
                [legacy.InlineKeyboardButton(text="▶️ Продолжить", callback_data=f"trial_proceed_{tid}")]
            ])
            await call.message.answer("Ищем доступные слоты на ближайшие 7 дней...", reply_markup=keyboard)
            return
        await call.message.edit_text("Ищем доступные слоты на ближайшие 7 дней...")
        await legacy.show_trial_dates(call, state, tid)
    elif len(subjects) > 1:
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(text=subj, callback_data=f"trial_subject_{subj}")]
            for subj in subjects
        ] + [[legacy.InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_tutors")]])
        if call.message.content_type != "text":
            await call.message.delete()
            await call.message.answer("Выберите предмет для пробного занятия:", reply_markup=keyboard)
            return
        await call.message.edit_text("Выберите предмет для пробного занятия:", reply_markup=keyboard)
        await state.set_state(legacy.TrialBookingStates.choosing_subject)
    else:
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_tutors")]
        ])
        if call.message.content_type != "text":
            await call.message.delete()
            await call.message.answer("У этого репетитора пока нет предметов.", reply_markup=keyboard)
        else:
            await call.message.edit_text("У этого репетитора пока нет предметов.", reply_markup=keyboard)


async def _legal_start_trials_booking(call, state):
    await legacy.safe_answer(call)
    if not await student_privacy_notice_completed(call.from_user.id, "telegram"):
        tid = int(call.data.split("_")[1])
        await state.clear()
        await state.update_data(legal_trial_tutor_id=tid)
        await record_student_privacy_presented(call.from_user.id, "telegram", "trial_booking")
        keyboard = legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(
                text="📄 Политика обработки ПД",
                url=DOCS["privacy_policy"]["url"],
            )],
            [legacy.InlineKeyboardButton(
                text="▶️ Продолжить пробную запись",
                callback_data="legal_continue_trial_booking",
            )],
            [legacy.InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu")],
        ])
        text = (
            "До начала записи на пробное занятие ознакомьтесь с Политикой обработки персональных данных. "
            "Для оформления записи Сервис использует необходимые идентификаторы и сведения о выбранном занятии."
        )
        if call.message.content_type != "text":
            await call.message.delete()
            await call.message.answer(text, reply_markup=keyboard)
        else:
            await call.message.edit_text(text, reply_markup=keyboard)
        return
    await _start_trial_after_privacy(call, state)


legacy.start_trials_booking.__code__ = _legal_start_trials_booking.__code__
legacy._start_trial_after_privacy = _start_trial_after_privacy


@legacy.dp.callback_query(legacy.F.data == "legal_continue_trial_booking")
async def legal_continue_trial_booking(call, state):
    await legacy.safe_answer(call)
    data = await state.get_data()
    tid = data.get("legal_trial_tutor_id")
    if not tid:
        await call.message.edit_text("Не удалось продолжить запись. Откройте карточку репетитора заново.")
        return
    await record_student_privacy_continued(call.from_user.id, "telegram", "trial_booking")
    call.data = f"trials_{tid}"
    await legacy._start_trial_after_privacy(call, state)


_original_set_pending_email_request = legacy.set_pending_email_request


async def _legal_set_pending_email_request(user_id: int, booking_id: int):
    try:
        await _send_student_legal_before_payment(
            user_id,
            booking_id if booking_id and booking_id > 0 else None,
        )
    except Exception:
        legacy.logging.exception("Не удалось показать документы перед запросом e-mail")
    return await _original_set_pending_email_request(user_id, booking_id)


legacy.set_pending_email_request = _legal_set_pending_email_request


_original_create_and_send_payment = legacy.create_and_send_payment


async def _legal_create_and_send_payment(source, bot, booking, email, booking_id):
    try:
        await _send_student_legal_before_payment(booking["user_id"], booking_id)
    except Exception:
        legacy.logging.exception("Не удалось показать документы перед оплатой booking=%s", booking_id)
    return await _original_create_and_send_payment(source, bot, booking, email, booking_id)


legacy.create_and_send_payment = _legal_create_and_send_payment


if hasattr(legacy, "create_subscription_payment"):
    _original_create_subscription_payment = legacy.create_subscription_payment

    async def _legal_create_subscription_payment(
        source, bot, user_id, tutor_id, subject, count, total, discount, email, user_platform
    ):
        try:
            await _send_student_legal_before_payment(user_id, None)
        except Exception:
            legacy.logging.exception("Не удалось показать документы перед оплатой абонемента")
        return await _original_create_subscription_payment(
            source, bot, user_id, tutor_id, subject, count, total, discount, email, user_platform
        )

    legacy.create_subscription_payment = _legal_create_subscription_payment


_paid_hook = install_payment_acceptance_hook()
legacy.mark_booking_paid_once = _paid_hook
_subscription_hook = install_subscription_acceptance_hook()
legacy.activate_subscription = _subscription_hook
