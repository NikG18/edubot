# core.py
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from database import (
    get_all_tutors,
    get_schedule,
    get_all_bookings,
    add_booking,
    update_booking,
    delete_booking,
    is_day_blocked,
    WEEKDAYS,
    WEEKDAY_NAMES
)

async def get_tutors_with_subjects() -> Dict[int, dict]:
    """Возвращает словарь всех преподавателей с предметами."""
    return await get_all_tutors()

async def get_available_dates(tutor_id: int, days_ahead: int = 30) -> List[str]:
    """Список дат, на которые есть хотя бы один свободный слот."""
    today = datetime.now()
    available = []
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        date_str = d.strftime("%d.%m.%Y")
        free = await get_available_slots(tutor_id, date_str)
        if free:
            available.append(date_str)
    return available

async def get_available_slots(tutor_id: int, date_str: str, exclude_booking_id: int = None) -> List[str]:
    """Свободные слоты на конкретную дату."""
    date = datetime.strptime(date_str, "%d.%m.%Y")
    day_name = WEEKDAYS[date.weekday()]
    schedule = await get_schedule(tutor_id)

    if day_name not in schedule:
        return []

    # Проверяем, не заблокирован ли день
    if await is_day_blocked(tutor_id, date_str):
        return []

    all_slots = schedule[day_name]
    busy = []
    bookings = await get_all_bookings()
    for bid, b in bookings.items():
        if b["tutor_id"] == tutor_id and b["date"] == date_str and b["status"] in ("pending", "confirmed"):
            if exclude_booking_id and bid == exclude_booking_id:
                continue
            busy.append(b["time_slot"])

    return [s for s in all_slots if s not in busy]

async def create_booking(tutor_id: int, user_id: int, username: str, subject: str, date_str: str, time_slot: str) -> int:
    """Создаёт бронирование и возвращает его ID."""
    return await add_booking(tutor_id, user_id, username, subject, date_str, time_slot)

async def get_student_bookings(user_id: int) -> List[dict]:
    """Возвращает активные записи ученика."""
    bookings = await get_all_bookings()
    user_bookings = []
    for bid, b in bookings.items():
        if b["user_id"] == user_id and b["status"] in ("pending", "confirmed"):
            b["id"] = bid  # добавляем ID для удобства
            user_bookings.append(b)
    return user_bookings

async def cancel_booking(booking_id: int) -> bool:
    """Отменяет бронирование, если это разрешено (за 24 часа)."""
    bookings = await get_all_bookings()
    booking = bookings.get(booking_id)
    if not booking or booking["status"] not in ("pending", "confirmed"):
        return False

    # Проверка на 24 часа
    date_str = booking["date"]
    time_part = booking["time_slot"].split("-")[0].replace(".", ":")
    dt = datetime.strptime(f"{date_str} {time_part}", "%d.%m.%Y %H:%M")
    if (dt - datetime.now()) <= timedelta(hours=24):
        return False

    await update_booking(booking_id, status="cancelled")
    return True

async def reschedule_booking(booking_id: int, new_date: str, new_time: str) -> bool:
    """Переносит бронирование: отменяет старое и создаёт новое (pending)."""
    bookings = await get_all_bookings()
    old = bookings.get(booking_id)
    if not old or old["status"] != "confirmed":
        return False

    # Проверка 24 часов
    date_str = old["date"]
    time_part = old["time_slot"].split("-")[0].replace(".", ":")
    dt = datetime.strptime(f"{date_str} {time_part}", "%d.%m.%Y %H:%M")
    if (dt - datetime.now()) <= timedelta(hours=24):
        return False

    # Отменяем старую
    await update_booking(booking_id, status="cancelled")

    # Создаём новую (pending)
    new_id = await add_booking(
        old["tutor_id"],
        old["user_id"],
        old["username"],
        old["subject"],
        new_date,
        new_time
    )
    return bool(new_id)
