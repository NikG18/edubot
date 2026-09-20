import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from booking_hub import (
    HUB, REGULAR, TRIAL, SUBSCRIPTION, install_telegram_booking_hub,
    install_vk_booking_hub, tg_markup, vk_markup, TrialOriginMiddleware,
)
from vkbottle import Keyboard, Callback


def payload(value):
    return json.loads(value) if isinstance(value, str) else value


class State:
    def __init__(self):
        self.data = {}
    async def clear(self):
        self.data = {}
    async def get_data(self):
        return self.data
    async def update_data(self, **kwargs):
        self.data.update(kwargs)


def namespace(code, **values):
    exec(code, values)
    return SimpleNamespace(**values), values


class BookingHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_trial_origin_survives_privacy_and_returns_to_booking(self):
        from aiogram.types import CallbackQuery, User
        state = State()
        call = CallbackQuery(id="1", from_user=User(id=77, is_bot=False, first_name="Student"),
                             chat_instance="chat", data="hub_trials_4")
        middleware = TrialOriginMiddleware()

        async def privacy(event, data):
            self.assertEqual(event.data, "trials_4")
            await data["state"].clear()
            await data["state"].update_data(legal_trial_tutor_id=4)

        await middleware(privacy, call, {"state": state})
        self.assertTrue(state.data["trial_from_hub"])
        handler = AsyncMock()
        await middleware(handler, call.model_copy(update={"data": "tutor_info_4"}), {"state": state})
        self.assertEqual(handler.await_args.args[0].data, TRIAL)
        handler.reset_mock()
        await middleware(handler, call.model_copy(update={"data": "trials_4"}), {"state": state})
        self.assertFalse(state.data["trial_from_hub"])
        handler.reset_mock()
        await middleware(handler, call.model_copy(update={"data": "tutor_info_4"}), {"state": state})
        self.assertEqual(handler.await_args.args[0].data, "tutor_info_4")

    def test_keyboard_rewrite_keeps_original_and_payment_links(self):
        original = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Back", callback_data="back_to_payment_menu")],
            [InlineKeyboardButton(text="Buy", callback_data="buy_tutor_1")],
        ])
        changed = tg_markup(original, {"back_to_payment_menu": HUB})
        self.assertEqual(changed.inline_keyboard[0][0].callback_data, HUB)
        self.assertEqual(original.inline_keyboard[0][0].callback_data, "back_to_payment_menu")
        self.assertEqual(changed.inline_keyboard[1][0].callback_data, "buy_tutor_1")
        kb = Keyboard(inline=True)
        kb.add(Callback("Tutor", payload={"cmd": "qr", "action": "subscription_tutor", "tutor_id": 4}))
        kb.row()
        kb.add(Callback("Back", payload={"cmd": "back_to_pay"}))
        raw = json.loads(vk_markup(kb.get_json(), subscription=True))
        payloads = [json.loads(b["action"]["payload"]) for row in raw["buttons"] for b in row]
        self.assertEqual(payloads[0], {"cmd": "qr", "action": "subscription_tutor",
                                      "tutor_id": 4, "booking_entry": True})
        self.assertEqual(payloads[1]["cmd"], HUB)

    async def test_telegram_menu_routes_and_subscription_return_origin(self):
        registered = []
        def register(*args):
            def decorate(fn):
                registered.append(fn)
                return fn
            return decorate

        async def regular(message, state):
            self.assertEqual(message.from_user.id, 77)
            await message.answer("privacy", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Next", callback_data="legal_continue_regular_booking")],
                [InlineKeyboardButton(text="Back", callback_data="back_to_menu")],
            ]))

        async def subscription(call, state):
            await state.clear()
            await call.message.edit_text("tutors", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Back", callback_data="back_to_payment_menu")],
            ]))

        legacy, globals_ = namespace(
            "async def zapis(message, state):\n    return await regular(message, state)\n"
            "async def buy_subscription_start(call, state):\n    return await subscription(call, state)\n",
            regular=regular, subscription=subscription,
        )
        register.outer_middleware = lambda middleware: None
        legacy.dp = SimpleNamespace(callback_query=register)
        legacy.F = F
        legacy.CallbackQuery = object
        legacy.FSMContext = object
        legacy.InlineKeyboardButton = InlineKeyboardButton
        legacy.InlineKeyboardMarkup = InlineKeyboardMarkup
        legacy.ReplyKeyboardRemove = ReplyKeyboardRemove
        legacy.safe_answer = AsyncMock()
        legacy.get_all_tutors = AsyncMock(return_value={1: {"name": "Tutor", "subjects": {"Math": 100}}})
        install_telegram_booking_hub(SimpleNamespace(legacy=legacy))
        globals_.update(vars(legacy))
        message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock(), from_user=SimpleNamespace(id=77))
        state = State()
        await legacy.zapis(message, state)
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual([r[0].callback_data for r in rows], [TRIAL, REGULAR, SUBSCRIPTION, "back_to_menu"])
        with patch.dict(sys.modules, {"legal_telegram": SimpleNamespace()}):
            call = SimpleNamespace(data=REGULAR, message=message, from_user=SimpleNamespace(id=77))
            await registered[0](call, state)
            rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
            self.assertEqual(rows[0][0].callback_data, "booking_regular_continue")
            self.assertEqual(rows[1][0].callback_data, HUB)
            call.data = TRIAL
            await registered[0](call, state)
            rows = message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            self.assertEqual(rows[0][0].callback_data, "hub_trials_1")
            self.assertEqual(rows[-1][0].callback_data, HUB)
            call.data = SUBSCRIPTION
            await registered[0](call, state)
            self.assertTrue(state.data["subscription_from_hub"])
            self.assertEqual(message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data, HUB)
            call.data = "back_to_buy_tutors"
            await legacy.buy_subscription_start(call, state)
            self.assertEqual(message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data, HUB)
            call.data = "buy_subscription"
            await legacy.buy_subscription_start(call, state)
            self.assertFalse(state.data["subscription_from_hub"])
            self.assertEqual(message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data, "back_to_payment_menu")

    async def test_vk_menu_regular_trial_and_subscription_origin(self):
        async def regular(message):
            self.assertEqual(message.from_id, 77)
            kb = Keyboard(inline=True)
            kb.add(Callback("Back", payload={"cmd": "back_to_menu"}))
            await message.answer("regular", keyboard=kb.get_json())

        legacy, globals_ = namespace(
            "async def zapis(message):\n    return await regular(message)\n", regular=regular)
        legacy.Keyboard, legacy.Callback = Keyboard, Callback
        original_edit = AsyncMock()
        legacy.edit_event_message = original_edit
        legacy.state_dispenser = SimpleNamespace(delete=AsyncMock(), get_data=AsyncMock(return_value={}), update=AsyncMock())
        legacy.get_all_tutors = AsyncMock(return_value={1: {"name": "Tutor", "subjects": {"Math": 100}}})
        install_vk_booking_hub(SimpleNamespace(legacy=legacy))
        globals_.update(vars(legacy))
        message = SimpleNamespace(from_id=77, answer=AsyncMock())
        await legacy.zapis(message)
        raw = json.loads(message.answer.await_args.kwargs["keyboard"])
        payloads = [payload(r[0]["action"]["payload"]) for r in raw["buttons"]]
        self.assertEqual(payloads[0]["cmd"], TRIAL)
        self.assertEqual(payloads[1]["cmd"], REGULAR)
        self.assertEqual(payloads[2], {"cmd": "qr", "action": "subscription_start", "booking_entry": True})
        event = SimpleNamespace(user_id=77, payload={"cmd": REGULAR})
        await legacy.booking_hub_entry(event)
        raw = json.loads(original_edit.await_args.kwargs["keyboard"])
        self.assertEqual(payload(raw["buttons"][0][0]["action"]["payload"])["cmd"], HUB)
        event.payload = {"cmd": TRIAL}
        await legacy.booking_hub_entry(event)
        raw = json.loads(original_edit.await_args.kwargs["keyboard"])
        self.assertEqual(payload(raw["buttons"][0][0]["action"]["payload"]), {"cmd": "trials", "tutor_id": 1, "trial_entry": True})
        event.payload = {"cmd": "trials", "tutor_id": 1, "trial_entry": True}
        kb = Keyboard(inline=True)
        kb.add(Callback("К анкете", payload={"cmd": "tutor_info_1"}))
        await legacy.edit_event_message(event, "dates", keyboard=kb.get_json())
        raw = json.loads(original_edit.await_args.kwargs["keyboard"])
        self.assertEqual(payload(raw["buttons"][0][0]["action"]["payload"])["cmd"], TRIAL)
        event.payload = {"cmd": "trials", "tutor_id": 1}
        await legacy.edit_event_message(event, "dates", keyboard=kb.get_json())
        self.assertEqual(original_edit.await_args.kwargs["keyboard"], kb.get_json())
        event.payload = {"cmd": "qr", "action": "subscription_start", "booking_entry": True}
        await legacy.edit_event_message(event, "email required")
        self.assertIn(HUB, original_edit.await_args.kwargs["keyboard"])


if __name__ == "__main__":
    unittest.main()
