from pathlib import Path
import sys

path = Path(sys.argv[1] if len(sys.argv) > 1 else 'vk_bot.py')
if not path.exists():
    raise SystemExit(f'Файл не найден: {path}')

s = path.read_text(encoding='utf-8')
original = s

def rep(old, new, count=1, required=True):
    global s
    if old not in s:
        if required:
            print('WARN: фрагмент не найден:', old[:120].replace('\n',' '))
        return
    s = s.replace(old, new, count)

# Новые функции БД и общие helpers.
rep('''    close_db, cleanup_old_bookings, WEEKDAYS, WEEKDAY_NAMES, get_tutor_by_vk_id\n)''',
'''    close_db, cleanup_old_bookings, WEEKDAYS, WEEKDAY_NAMES, get_tutor_by_vk_id,\n    get_booked_slots, mark_booking_paid_once, mark_booking_payment_failed, reschedule_booking, move_booking_in_place\n)''')
rep('''from messaging import send_to_user, send_to_tutor, send_telegram_message, send_telegram_message_get_id''',
'''from messaging import (\n    send_to_user, send_to_tutor, send_telegram_message,\n    send_telegram_message_get_id, close_messaging\n)\nfrom bot_common import now_msk_naive, valid_email''')

# Единая логика свободных слотов и фильтрация прошедшего времени.
start = s.find('async def get_available_slots(')
end = s.find('\ndef clean_time_input', start)
if start != -1 and end != -1:
    s = s[:start] + '''async def get_available_slots(tutor_id: int, date_str: str, exclude_booking_id: int = None) -> list:\n    try:\n        date = datetime.strptime(date_str, "%d.%m.%Y")\n    except ValueError:\n        return []\n    day_name = WEEKDAYS[date.weekday()]\n    if await is_day_blocked(tutor_id, day_name):\n        return []\n    schedule = await get_schedule(tutor_id)\n    all_slots = schedule.get(day_name, [])\n    if not all_slots:\n        return []\n    busy = set(await get_booked_slots(tutor_id, date_str, exclude_booking_id))\n    now = now_msk_naive()\n    result = []\n    for slot in all_slots:\n        if slot in busy:\n            continue\n        try:\n            start_time = slot.split("-")[0].replace(".", ":")\n            slot_dt = datetime.strptime(f"{date_str} {start_time}", "%d.%m.%Y %H:%M")\n        except (ValueError, AttributeError):\n            continue\n        if slot_dt > now:\n            result.append(slot)\n    return result\n\n\nasync def get_available_dates(tutor_id: int, days_ahead=30) -> list:\n    today = now_msk_naive().replace(hour=0, minute=0, second=0, microsecond=0)\n    available = []\n    for i in range(days_ahead):\n        d = today + timedelta(days=i)\n        date_str = d.strftime("%d.%m.%Y")\n        if await get_available_slots(tutor_id, date_str):\n            available.append(date_str)\n    return available\n\n''' + s[end:]
else:
    print('WARN: не найден блок get_available_slots/get_available_dates')

# Время Москвы вместо времени сервера.
s = s.replace('datetime.now()', 'now_msk_naive()')
s = s.replace('now_msk_naive()(msk)', 'datetime.now(msk)')

# Пробное занятие: payload и router должны совпадать.
rep('''row.append(Callback(s, payload={"cmd": f"trial_slot_{s}"}))''',
    '''row.append(Callback(s, payload={"cmd": "trial_slot", "slot": s}))''')
# Router уже ждёт cmd == trial_slot, поэтому после этого работает.

# choose_date: в исходнике не отправлялась клавиатура и не ставился state.
needle = '''    kb.add(Callback("🔙 К выбору даты", payload={"cmd": "back_to_date"}))\n\n\nasync def back_to_date(event: MessageEvent):'''
replacement = '''    kb.add(Callback("🔙 К выбору даты", payload={"cmd": "back_to_date"}))\n    await edit_event_message(event, "Выберите время:", keyboard=kb.get_json())\n    await state_dispenser.set(event.user_id, BookingStates.waiting_time)\n\n\nasync def back_to_date(event: MessageEvent):'''
rep(needle, replacement)

# Ошибка префикса callback для связи преподавателя с учеником.
rep('''elif cmd.startswith("tutorcontactstudent_"):\n        await tutor_contact_student_chosen(event)''',
    '''elif cmd.startswith("tutor_contact_student_"):\n        await tutor_contact_student_chosen(event)''')

# Helpers авторизации вставляем перед обработчиками начала диалога.
marker = '# -------------------- Обработчики начала диалога и главного меню --------------------'
if marker in s and '_require_vk_booking_owner' not in s:
    helpers = '''async def _require_vk_booking_owner(event: MessageEvent, booking: dict) -> bool:\n    if not booking or booking.get("user_id") != event.user_id or booking.get("user_platform", "vk") != "vk":\n        await answer_event(event, "Доступ запрещён.", snackbar=True)\n        return False\n    return True\n\n\nasync def _require_vk_booking_tutor(event: MessageEvent, booking: dict) -> bool:\n    if not booking:\n        await answer_event(event, "Запись не найдена.", snackbar=True)\n        return False\n    tid = await get_tutor_by_vk_id(event.user_id)\n    if tid != booking.get("tutor_id"):\n        await answer_event(event, "Доступ запрещён.", snackbar=True)\n        return False\n    return True\n\n\n'''
    s = s.replace(marker, helpers + marker, 1)

# Двойное бронирование: add_booking возвращает None при конфликте.
rep("""    new_id = await add_booking(tid, uid, username, subject, date, slot, user_platform='vk')\n\n    booking_msg = (""",
"""    new_id = await add_booking(tid, uid, username, subject, date, slot, user_platform='vk')\n    if new_id is None:\n        await edit_event_message(event, "⚠️ Этот слот уже заняли. Выберите другое время.")\n        await state_dispenser.delete(event.user_id)\n        return\n\n    booking_msg = (""", count=2)

# Проверка владельца при отмене и переносе ученика.
rep('''    if not booking:\n        await edit_event_message(event, "Запись не найдена.")\n        return\n\n    dt = datetime.strptime''',
'''    if not booking:\n        await edit_event_message(event, "Запись не найдена.")\n        return\n    if not await _require_vk_booking_owner(event, booking):\n        return\n\n    dt = datetime.strptime''', count=1)

rep('''    if not booking or booking["status"] != "confirmed":\n        await edit_event_message(event, "Запись недоступна для переноса.")\n        return''',
'''    if not booking:\n        await edit_event_message(event, "Запись не найдена.")\n        return\n    if not await _require_vk_booking_owner(event, booking):\n        return\n    if booking["status"] != "confirmed":\n        await edit_event_message(event, "Запись недоступна для переноса.")\n        return\n    if booking.get("tinkoff_payment_id"):\n        await edit_event_message(event, "Для записи уже создан платёж. Для безопасного переноса обратитесь в поддержку.")\n        return''', count=1)

# Атомарный перенос ученика.
rep('''    await update_booking(old_bid, status="cancelled")\n    new_id = await add_booking(tid, student_id, student_username, subject, new_date, new_time, user_platform='vk')''',
'''    new_id = await reschedule_booking(old_bid, new_date, new_time, new_status="pending")\n    if new_id is None:\n        await edit_event_message(event, "⚠️ Новый слот уже занят. Старая запись сохранена.")\n        await state_dispenser.delete(event.user_id)\n        return''', count=1)

# Tutor callbacks: null check + authorization.
rep('''    booking = bookings.get(bid)\n    if not booking or booking["status"] != "pending":\n        await edit_event_message(event, "Заявка уже обработана.")\n        return\n\n    user_id = booking["user_id"]''',
'''    booking = bookings.get(bid)\n    if not booking:\n        await edit_event_message(event, "Заявка не найдена.")\n        return\n    if not await _require_vk_booking_tutor(event, booking):\n        return\n    if booking["status"] != "pending":\n        await edit_event_message(event, "Заявка уже обработана.")\n        return\n\n    user_id = booking["user_id"]''', count=1)

rep('''    booking = bookings.get(bid)\n    if not booking or booking["status"] != "pending":\n        await edit_event_message(event, "Заявка уже обработана.")\n        return\n    user_id = booking["user_id"]''',
'''    booking = bookings.get(bid)\n    if not booking:\n        await edit_event_message(event, "Заявка не найдена.")\n        return\n    if not await _require_vk_booking_tutor(event, booking):\n        return\n    if booking["status"] != "pending":\n        await edit_event_message(event, "Заявка уже обработана.")\n        return\n    user_id = booking["user_id"]''', count=1)

# tutor_cancel bug: booking[
# tutor_cancel: сначала проверяем наличие записи, затем права и статус.
rep('''    booking = bookings.get(bid)\n    user_id = booking["user_id"]\n    if not booking or booking["status"] != "confirmed":\n        await edit_event_message(event, "Невозможно отменить.")\n        return''',
'''    booking = bookings.get(bid)\n    if not booking:\n        await edit_event_message(event, "Запись не найдена.")\n        return\n    if not await _require_vk_booking_tutor(event, booking):\n        return\n    user_id = booking["user_id"]\n    if booking["status"] != "confirmed":\n        await edit_event_message(event, "Невозможно отменить.")\n        return''')

# tutor_reschedule: авторизация.
rep('''    booking = bookings.get(bid)\n    if not booking or booking["status"] != "confirmed":\n        await edit_event_message(event, "Невозможно перенести.")\n        return''',
'''    booking = bookings.get(bid)\n    if not booking:\n        await edit_event_message(event, "Запись не найдена.")\n        return\n    if not await _require_vk_booking_tutor(event, booking):\n        return\n    if booking["status"] != "confirmed":\n        await edit_event_message(event, "Невозможно перенести.")\n        return''', count=1)

# Атомарный перенос преподавателем.
rep('''    await update_booking(old_bid, status="cancelled")\n    new_id = await add_booking(tid, student_id, student_username, subject, new_date, new_time, user_platform='vk')\n    await update_booking(new_id, status="confirmed", reminded=0)''',
'''    moved = await move_booking_in_place(old_bid, new_date, new_time)\n    if not moved:\n        await edit_event_message(event, "⚠️ Новый слот уже занят. Старая запись сохранена.")\n        await state_dispenser.delete(event.user_id)\n        return\n    new_id = old_bid''')

# Цена фиксируется после первой генерации платежа.
rep('''    price_rub = tutor["subjects"].get(booking["subject"])\n    if not price_rub:\n        return\n    amount_kop = price_rub * 100''',
'''    if booking.get("amount"):\n        amount_kop = int(booking["amount"])\n        price_rub = amount_kop / 100\n    else:\n        price_rub = tutor["subjects"].get(booking["subject"])\n        if not price_rub:\n            return\n        amount_kop = int(price_rub) * 100''')

# Передаём ИНН и уникальный префикс также из VK.
rep('''        tutor_name=tutor["name"],\n        customer_email=email\n    )''',
'''        tutor_name=tutor["name"],\n        customer_email=email,\n        inn=tutor.get("inn", ""),\n        order_id_prefix="booking"\n    )''')

# В email обработчике используем нормальную валидацию.
rep('''    email = message.text.strip()\n    if "@" not in email or "." not in email:\n        return''',
'''    email = message.text.strip()\n    if not valid_email(email):\n        await message.answer("Введите корректный email, например name@example.com")\n        return''')

# Идемпотентная проверка платежей вместо повторного add_lesson_to_balance каждый час.
start = s.find('async def check_pending_payments():')
end = s.find('\n\nasync def send_reminders():', start)
if start != -1 and end != -1:
    s = s[:start] + '''async def check_pending_payments():\n    bookings = await get_all_bookings()\n    for bid, b in bookings.items():\n        if b.get("status") != "confirmed" or not b.get("tinkoff_payment_id"):\n            continue\n        payment_state = await check_payment(b["tinkoff_payment_id"])\n        status = payment_state.get("Status")\n        if payment_state.get("Success") and status in ("CONFIRMED", "AUTHORIZED"):\n            changed, booking = await mark_booking_paid_once(bid)\n            if changed and booking:\n                await send_to_user(booking["user_id"], booking.get("user_platform", "vk"),\n                                   "✅ Оплата получена! Занятие подтверждено.")\n                await send_to_tutor(booking["tutor_id"],\n                                    f"✅ Оплата за занятие {booking['date']} {booking['time_slot']} получена.")\n        elif status in ("REJECTED", "CANCELED"):\n            changed, booking = await mark_booking_payment_failed(bid)\n            if changed and booking:\n                await send_to_user(booking["user_id"], booking.get("user_platform", "vk"),\n                                   "❌ Платёж не прошёл. Запись отменена.")\n''' + s[end:]
else:
    print('WARN: не найден check_pending_payments')

# Напоминание не должно срабатывать на уже прошедший урок и не должно требовать попадания ровно в 1 минуту.
s = s.replace('if timedelta(minutes=59) < diff <= timedelta(hours=1):',
              'if timedelta(0) < diff <= timedelta(hours=1):')

# Универсальный callback: защищаем админ-команды до маршрутизации.
needle = '''    user_id = event.user_id\n\n    # --- Навигация и общие действия ---'''
replacement = '''    user_id = event.user_id\n\n    admin_prefixes = (\n        "admin_", "edit_tutor_", "edit_", "manage_subjects", "back_to_edit_tutor",\n        "add_subject", "editsubj_", "confirm_delete_subject", "back_to_subjects_list",\n        "toggle_commission_mode", "del_tutor_", "confirm_delete", "add_another_subject",\n        "finish_adding_subjects"\n    )\n    if cmd.startswith(admin_prefixes) and user_id != ADMIN_VK_ID:\n        await answer_event(event, "Доступ запрещён.", snackbar=True)\n        return\n\n    # --- Навигация и общие действия ---'''
rep(needle, replacement)

# Права на панели преподавателя: прямой callback нельзя использовать для чужого tutor_id.
rep('''async def show_students(event: MessageEvent):\n    tid = int(event.payload["cmd"].split("_")[-1])\n    bookings = await get_all_bookings()''',
'''async def show_students(event: MessageEvent):\n    tid = int(event.payload["cmd"].split("_")[-1])\n    if await get_tutor_by_vk_id(event.user_id) != tid:\n        await answer_event(event, "Доступ запрещён.", snackbar=True)\n        return\n    bookings = await get_all_bookings()''')
rep('''async def schedule_main(event: MessageEvent):\n    tid = int(event.payload["cmd"].split("_")[-1])\n    await state_dispenser.update(event.user_id, tid=tid)''',
'''async def schedule_main(event: MessageEvent):\n    tid = int(event.payload["cmd"].split("_")[-1])\n    if await get_tutor_by_vk_id(event.user_id) != tid:\n        await answer_event(event, "Доступ запрещён.", snackbar=True)\n        return\n    await state_dispenser.update(event.user_id, tid=tid)''')
rep('''async def tutor_stats_menu(event: MessageEvent):\n    tid = int(event.payload["cmd"].split("_")[2])\n    fin = await get_tutor_financials(tid)''',
'''async def tutor_stats_menu(event: MessageEvent):\n    tid = int(event.payload["cmd"].split("_")[2])\n    if await get_tutor_by_vk_id(event.user_id) != tid:\n        await answer_event(event, "Доступ запрещён.", snackbar=True)\n        return\n    fin = await get_tutor_financials(tid)''')
rep('''async def tutor_stats_month(event: MessageEvent):\n    parts = event.payload["cmd"].split("_")\n    tid = int(parts[3])\n    year = int(parts[4])''',
'''async def tutor_stats_month(event: MessageEvent):\n    parts = event.payload["cmd"].split("_")\n    tid = int(parts[3])\n    if await get_tutor_by_vk_id(event.user_id) != tid:\n        await answer_event(event, "Доступ запрещён.", snackbar=True)\n        return\n    year = int(parts[4])''')

# Закрытие HTTP session/DB при завершении, если run_polling возвращает/бросает исключение.
rep('''async def main():\n    await init_db()\n    asyncio.create_task(periodic_cleanup())\n    asyncio.create_task(reminder_loop())\n    asyncio.create_task(pending_reminder_loop())\n    await bot.run_polling()''',
'''async def main():\n    await init_db()\n    asyncio.create_task(periodic_cleanup())\n    asyncio.create_task(reminder_loop())\n    asyncio.create_task(pending_reminder_loop())\n    try:\n        await bot.run_polling()\n    finally:\n        await close_messaging()\n        await close_db()''')

path.write_text(s, encoding='utf-8')
print(f'Готово: {path}')
print(f'Изменено символов: {sum(a != b for a, b in zip(original, s)) + abs(len(original)-len(s))}')
