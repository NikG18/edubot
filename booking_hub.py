"""Additional booking entry points; existing booking/payment handlers stay in use."""
import json
from types import FunctionType

HUB = "booking_hub"
REGULAR = "booking_regular"
TRIAL = "booking_trial"
SUBSCRIPTION = "booking_subscription"


class TrialOriginMiddleware:
    async def __call__(self, handler, event, data):
        state = data.get("state")
        command = event.data or ""
        if state is None:
            return await handler(event, data)
        values = await state.get_data()
        origin = bool(values.get("trial_from_hub"))
        if command.startswith("hub_trials_"):
            origin = True
            event = event.model_copy(update={"data": command.removeprefix("hub_")})
        elif command.startswith("trials_"):
            origin = False
        if command.startswith(("hub_trials_", "trials_")):
            await state.update_data(trial_from_hub=origin)
        if origin and (command == "back_to_tutors" or command.startswith("tutor_info_")):
            event = event.model_copy(update={"data": TRIAL})
        is_trial = command.startswith(("hub_trials_", "trial", "legal_continue_trial", "back_to_trial"))
        if origin and is_trial:
            tid = values.get("tutor_id") or values.get("legal_trial_tutor_id")
            if command.startswith(("hub_trials_", "trials_")):
                tid = command.rsplit("_", 1)[-1]
            event = tg_call(event, {
                "back_to_tutors": TRIAL, "back_to_menu": TRIAL,
                f"tutor_info_{tid}": TRIAL,
            })
        result = await handler(event, data)
        # Privacy continuation clears state; retain origin for its next screen.
        if origin and is_trial and await state.get_data():
            await state.update_data(trial_from_hub=True)
        return result


def clone(fn):
    return FunctionType(fn.__code__, fn.__globals__, fn.__name__,
                        fn.__defaults__, fn.__closure__)


class Proxy:
    def __init__(self, source, **values):
        self._source = source
        self.__dict__.update(values)

    def __getattr__(self, name):
        return getattr(self._source, name)


def tg_markup(markup, mapping):
    if markup is None or not hasattr(markup, "inline_keyboard"):
        return markup
    markup = markup.model_copy(deep=True)
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data in mapping:
                button.callback_data = mapping[button.callback_data]
                if button.callback_data == TRIAL:
                    button.text = "🔙 К выбору преподавателя"
    return markup


def tg_call(call, mapping):
    async def edit(text, **kwargs):
        kwargs["reply_markup"] = tg_markup(kwargs.get("reply_markup"), mapping)
        return await call.message.edit_text(text, **kwargs)

    async def answer(text, **kwargs):
        kwargs["reply_markup"] = tg_markup(kwargs.get("reply_markup"), mapping)
        return await call.message.answer(text, **kwargs)

    return Proxy(call, message=Proxy(call.message, edit_text=edit, answer=answer))


def vk_markup(keyboard, *, subscription=False):
    if not keyboard:
        return keyboard
    value = json.loads(keyboard)
    for row in value.get("buttons", []):
        for button in row:
            action = button.get("action", {})
            payload = action.get("payload")
            if not payload:
                continue
            payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
            if payload.get("cmd") in {"back_to_menu", "back_to_pay"}:
                payload["cmd"] = HUB
                action["label"] = "🔙 К записи на занятия"
            if subscription and payload.get("cmd") == "qr":
                payload["booking_entry"] = True
            action["payload"] = json.dumps(payload, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


async def _tg_hub_message(message, state):
    return await _booking_hub_message(message, state)


async def _vk_hub_message(message):
    return await _booking_hub_message(message)


async def _tg_subscription_entry(call, state):
    return await _booking_subscription_entry(call, state)


def install_telegram_booking_hub(app):
    legacy = app.legacy
    if getattr(legacy, "_booking_hub_installed", False):
        return
    regular_start = clone(legacy.zapis)
    subscription_start = clone(legacy.buy_subscription_start)
    legacy.dp.callback_query.outer_middleware(TrialOriginMiddleware())

    def keyboard():
        return legacy.InlineKeyboardMarkup(inline_keyboard=[
            [legacy.InlineKeyboardButton(text=text, callback_data=cmd)]
            for text, cmd in (
                ("🎓 Запись на пробное занятие", TRIAL),
                ("📅 Запись на обычное занятие", REGULAR),
                ("🎟 Купить абонемент", SUBSCRIPTION),
                ("🔙 В главное меню", "back_to_menu"),
            )
        ])

    async def message_start(message, state):
        await state.clear()
        await message.answer("Переходим в раздел...", reply_markup=legacy.ReplyKeyboardRemove())
        await message.answer("Выберите вариант записи:", reply_markup=keyboard())

    async def subscription_entry(call, state):
        data = await state.get_data()
        from_hub = call.data == SUBSCRIPTION or (
            call.data == "back_to_buy_tutors" and data.get("subscription_from_hub")
        )
        mapping = {
            "back_to_payment_menu": HUB,
            "legal_continue_subscription_purchase": "booking_subscription_continue",
        } if from_hub else {}
        result = await subscription_start(tg_call(call, mapping), state)
        await state.update_data(subscription_from_hub=bool(from_hub))
        return result

    legacy._booking_hub_message = message_start
    legacy.zapis.__code__ = _tg_hub_message.__code__
    legacy._booking_subscription_entry = subscription_entry
    legacy.buy_subscription_start.__code__ = _tg_subscription_entry.__code__

    @legacy.dp.callback_query(legacy.F.data.in_({
        HUB, REGULAR, TRIAL, SUBSCRIPTION,
        "booking_regular_continue", "booking_subscription_continue",
    }))
    async def booking_entry(call: legacy.CallbackQuery, state: legacy.FSMContext):
        import legal_telegram
        await legacy.safe_answer(call)
        if call.data == HUB:
            await state.clear()
            await call.message.edit_text("Выберите вариант записи:", reply_markup=keyboard())
        elif call.data == TRIAL:
            await state.clear()
            tutors = await legacy.get_all_tutors()
            rows = [[legacy.InlineKeyboardButton(
                text=tutor["name"], callback_data=f"hub_trials_{tid}"
            )] for tid, tutor in tutors.items() if tutor.get("subjects")]
            rows.append([legacy.InlineKeyboardButton(text="🔙 Назад", callback_data=HUB)])
            await call.message.edit_text(
                "Выберите преподавателя для пробного занятия:",
                reply_markup=legacy.InlineKeyboardMarkup(inline_keyboard=rows),
            )
        elif call.data == SUBSCRIPTION:
            await state.clear()
            await legacy.buy_subscription_start(call, state)
        elif call.data == "booking_subscription_continue":
            await legal_telegram.legal_continue_subscription_purchase(
                tg_call(call, {"back_to_payment_menu": HUB}), state
            )
            await state.update_data(subscription_from_hub=True)
        elif call.data == "booking_regular_continue":
            await legal_telegram.legal_continue_regular_booking(
                tg_call(call, {"back_to_menu": HUB}), state
            )
        else:
            adapted = tg_call(call, {
                "back_to_menu": HUB,
                "legal_continue_regular_booking": "booking_regular_continue",
            })
            message = Proxy(adapted.message, from_user=call.from_user)
            await regular_start(message, state)

    legacy._booking_hub_installed = True


def install_vk_booking_hub(app):
    legacy = app.legacy
    if getattr(legacy, "_booking_hub_installed", False):
        return
    regular_start = clone(legacy.zapis)
    original_edit = legacy.edit_event_message

    def keyboard():
        kb = legacy.Keyboard(inline=True)
        for index, (text, payload) in enumerate((
            ("🎓 Запись на пробное занятие", {"cmd": TRIAL}),
            ("📅 Запись на обычное занятие", {"cmd": REGULAR}),
            ("🎟 Купить абонемент", {"cmd": "qr", "action": "subscription_start", "booking_entry": True}),
            ("🔙 В главное меню", {"cmd": "back_to_menu"}),
        )):
            if index:
                kb.row()
            kb.add(legacy.Callback(text, payload=payload))
        return kb.get_json()

    async def message_start(message):
        await legacy.state_dispenser.delete(message.from_id)
        await message.answer("Выберите вариант записи:", keyboard=keyboard())

    async def edit(event, text, keyboard=None, **kwargs):
        kwargs["keyboard"] = keyboard
        values = await legacy.state_dispenser.get_data(event.user_id)
        trial_command = str((event.payload or {}).get("cmd") or "")
        if (trial_command.startswith("trial") or trial_command == "back_to_trial_dates") and (
            (event.payload or {}).get("trial_entry") or values.get("trial_from_hub")
        ):
            if keyboard:
                value = json.loads(keyboard)
                for row in value.get("buttons", []):
                    for button in row:
                        action = button.get("action", {})
                        payload = action.get("payload")
                        if not payload:
                            continue
                        payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
                        cmd = payload.get("cmd", "")
                        if cmd == "back_to_tutors" or cmd.startswith("tutor_info_"):
                            payload = {"cmd": TRIAL}
                            action["label"] = "🔙 К выбору преподавателя"
                        elif cmd.startswith("trial"):
                            payload["trial_entry"] = True
                        action["payload"] = json.dumps(payload, ensure_ascii=False)
                kwargs["keyboard"] = json.dumps(value, ensure_ascii=False)
            await legacy.state_dispenser.update(event.user_id, trial_from_hub=True)
        if (event.payload or {}).get("booking_entry"):
            kwargs["keyboard"] = vk_markup(kwargs.get("keyboard"), subscription=True)
            if not kwargs["keyboard"]:
                kb = legacy.Keyboard(inline=True)
                kb.add(legacy.Callback("🔙 К записи на занятия", payload={"cmd": HUB}))
                kwargs["keyboard"] = kb.get_json()
        return await original_edit(event, text, **kwargs)

    async def entry(event):
        cmd = event.payload.get("cmd")
        await legacy.state_dispenser.delete(event.user_id)
        if cmd == HUB:
            return await original_edit(event, "Выберите вариант записи:", keyboard=keyboard())
        if cmd == TRIAL:
            tutors = await legacy.get_all_tutors()
            kb = legacy.Keyboard(inline=True)
            for tid, tutor in tutors.items():
                if not tutor.get("subjects"):
                    continue
                kb.add(legacy.Callback(tutor["name"], payload={"cmd": "trials", "tutor_id": int(tid), "trial_entry": True}))
                kb.row()
            kb.add(legacy.Callback("🔙 Назад", payload={"cmd": HUB}))
            return await original_edit(event, "Выберите преподавателя для пробного занятия:", keyboard=kb.get_json())

        async def answer(text, **kwargs):
            kwargs["keyboard"] = vk_markup(kwargs.get("keyboard"))
            return await original_edit(event, text, **kwargs)

        await regular_start(Proxy(event, from_id=event.user_id, answer=answer))

    legacy._booking_hub_message = message_start
    legacy.zapis.__code__ = _vk_hub_message.__code__
    legacy.booking_hub_entry = entry
    legacy.edit_event_message = edit
    legacy._booking_hub_installed = True
