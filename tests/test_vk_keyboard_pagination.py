import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from vkbottle import API, Callback, Keyboard, OpenLink

import vk_keyboard_pagination as pager
from test_booking_session_reset import load_handler


def payload(button):
    value = button["action"].get("payload", {})
    return json.loads(value) if isinstance(value, str) else value


def keyboard(count, width=1, back=True):
    kb = Keyboard(inline=True)
    for index in range(count):
        if index and index % width == 0:
            kb.row()
        kb.add(Callback(f"Предмет {index}", {"cmd": f"subject_1_{index}", "trial_entry": True}))
    if back:
        kb.row().add(Callback("🔙 Назад в меню", {"cmd": "back_to_menu"}))
    return kb.get_json()


class KeyboardLayoutTests(unittest.TestCase):
    def assert_valid(self, data):
        rows = data["buttons"]
        self.assertLessEqual(len(rows), 6)
        self.assertLessEqual(sum(map(len, rows)), 10)
        self.assertTrue(all(0 < len(row) <= 5 for row in rows))

    def test_large_lists_keep_all_actions_and_back_on_every_page(self):
        for count in (0, 5, 6, 10, 13, 30, 100):
            for width in (1, 3, 6, 12):
                with self.subTest(count=count, width=width):
                    source = keyboard(count, width)
                    pages = pager.split_keyboard(source)
                    actions = []
                    for index in range(len(pages)):
                        data = json.loads(pager.page_keyboard(pages, "a" * 32, index))
                        self.assert_valid(data)
                        values = [payload(b) for row in data["buttons"] for b in row]
                        self.assertIn({"cmd": "back_to_menu"}, values)
                        actions.extend(v for v in values if v.get("cmd", "").startswith("subject_"))
                        for v in values:
                            if v.get("cmd") == pager.PAGE_COMMAND:
                                self.assertTrue(0 <= v["page"] < len(pages))
                    expected = [payload(b) for row in json.loads(source)["buttons"] for b in row
                                if payload(b).get("cmd", "").startswith("subject_")]
                    self.assertEqual(actions, expected)

    def test_links_and_payloads_survive_without_mutating_input(self):
        kb = Keyboard(inline=True)
        for index in range(12):
            kb.add(OpenLink(f"https://example.com/{index}", f"Документ {index}"))
            kb.row()
        data = json.loads(kb.get_json())
        original = json.dumps(data)
        pages = pager.split_keyboard(data)
        buttons = [b for page in pages for row in page["buttons"] for b in row]
        self.assertEqual([b["action"]["link"] for b in buttons], [f"https://example.com/{i}" for i in range(12)])
        self.assertEqual(json.dumps(data), original)

    def test_small_and_reply_keyboards_are_not_paginated(self):
        for inline in (False, True):
            source = json.loads(keyboard(5))
            source["inline"] = inline
            self.assertEqual(pager.split_keyboard(source), [source])


class KeyboardTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_platform_notification_transport_is_paginated(self):
        import archive.messaging_legacy as messaging

        class Response:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def json(self, **kwargs):
                return {"response": 1}

        from unittest.mock import Mock
        session = SimpleNamespace(post=Mock(return_value=Response()))
        with patch.object(messaging, "VK_BOT_TOKEN", "test"), \
             patch.object(messaging, "_get_session", AsyncMock(return_value=session)), \
             patch.object(pager, "save_pages", AsyncMock(return_value="a" * 32)) as save:
            self.assertTrue(await messaging.send_vk_message(123, "Занятия", keyboard(30)))
        sent = session.post.call_args.kwargs["data"]
        KeyboardLayoutTests().assert_valid(json.loads(sent["keyboard"]))
        self.assertEqual(save.await_args.args[0], 123)

    async def test_real_subject_builder_with_more_than_five_subjects(self):
        ns = dict(Keyboard=Keyboard, Callback=Callback,
                  get_all_tutors=AsyncMock(return_value={1: {"subjects": {f"Предмет {i}": 1000 for i in range(17)}}}))
        build = load_handler("vk_bot_legacy.py", ns, "make_subjects_keyboard")
        raw = await build(1)
        with patch.object(pager, "save_pages", AsyncMock(return_value="a" * 32)) as save:
            output = await pager.prepare_message({"peer_id": 123, "message": "Предметы", "keyboard": raw})
        KeyboardLayoutTests().assert_valid(json.loads(output["keyboard"]))
        pages = save.await_args.args[1]["pages"]
        commands = [payload(b)["cmd"] for p in pages for row in p["buttons"] for b in row]
        self.assertEqual(len({c for c in commands if c.startswith("subject_")}), 17)

    async def test_api_send_and_edit_paginate_after_payload_rewrites(self):
        for method in ("messages.send", "messages.edit"):
            with patch.object(pager, "save_pages", AsyncMock(return_value="a" * 32)) as save, \
                 patch.object(API, "request", AsyncMock(return_value={"response": 1})) as request:
                api = pager.PaginatedAPI("test")
                data = {"peer_id": 123, "message": "Список", "keyboard": keyboard(30), "attachment": "photo1_2"}
                await api.request(method, data)
                sent = request.await_args.args[1]
                KeyboardLayoutTests().assert_valid(json.loads(sent["keyboard"]))
                self.assertEqual(sent["attachment"], "photo1_2")
                self.assertEqual(save.await_args.args[0], 123)
                self.assertEqual(data["keyboard"], keyboard(30))

    async def test_paging_survives_new_process_and_does_not_change_flow(self):
        snapshot = {"pages": pager.split_keyboard(keyboard(17)), "message": "Предметы", "attachment": "photo1_2"}
        event = SimpleNamespace(peer_id=123, user_id=123, conversation_message_id=42,
                                payload={"cmd": pager.PAGE_COMMAND, "token": "a" * 32, "page": 1})
        legacy = SimpleNamespace(bot=SimpleNamespace(api=SimpleNamespace(messages=SimpleNamespace(edit=AsyncMock()))),
                                 answer_event=AsyncMock(), state_dispenser=SimpleNamespace(delete=AsyncMock(), update=AsyncMock()))
        with patch.object(pager, "load_pages", AsyncMock(return_value=snapshot)) as load:
            await pager.show_page(legacy, event)
        load.assert_awaited_once_with("a" * 32, 123)
        edited = legacy.bot.api.messages.edit.await_args.kwargs
        self.assertEqual(edited["conversation_message_id"], 42)
        self.assertEqual(edited["message"], "Предметы")
        self.assertEqual(edited["attachment"], "photo1_2")
        KeyboardLayoutTests().assert_valid(json.loads(edited["keyboard"]))
        legacy.state_dispenser.delete.assert_not_awaited()
        legacy.state_dispenser.update.assert_not_awaited()

    async def test_expired_foreign_and_invalid_pages_do_not_edit(self):
        for peer, user, page, snapshot in ((123, 456, 0, {}), (123, 123, 0, None),
                                          (123, 123, -1, {"pages": [{}]}), (123, 123, 99, {"pages": [{}]})):
            event = SimpleNamespace(peer_id=peer, user_id=user,
                                    payload={"token": "a" * 32, "page": page})
            legacy = SimpleNamespace(answer_event=AsyncMock(), bot=SimpleNamespace(api=SimpleNamespace(messages=SimpleNamespace(edit=AsyncMock()))))
            with patch.object(pager, "load_pages", AsyncMock(return_value=snapshot)):
                await pager.show_page(legacy, event)
            legacy.bot.api.messages.edit.assert_not_awaited()
            legacy.answer_event.assert_awaited_once()
