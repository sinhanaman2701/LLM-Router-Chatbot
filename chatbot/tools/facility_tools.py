from __future__ import annotations

import datetime
from typing import Any


def _time_slots(open_time: str, close_time: str, duration_min: int) -> list[str]:
    fmt = "%H:%M"
    start = datetime.datetime.strptime(open_time, fmt)
    end = datetime.datetime.strptime(close_time, fmt)
    delta = datetime.timedelta(minutes=duration_min)
    slots = []
    current = start
    while current + delta <= end:
        slots.append(current.strftime(fmt))
        current += delta
    return slots


async def get_facility_list(adapter: Any) -> list[dict]:
    return await adapter.get_facility_list()


async def get_facility_availability(
    adapter: Any,
    facility_id: str,
    date: str,
) -> dict:
    data = await adapter.get_facility_booking_data(facility_id)
    if isinstance(data, list):
        bookings = data
        open_time = "07:00"
        close_time = "22:00"
        duration_min = 60
    else:
        bookings = data.get("bookings", [])
        open_time = data.get("open_time", "07:00")
        close_time = data.get("close_time", "22:00")
        duration_min = data.get("default_duration_min", 60)

    occupied: set[str] = set()
    for b in bookings:
        b_date = b.get("date", "")
        b_start = b.get("start_time", "")
        if b_date == date and b_start:
            occupied.add(b_start)

    all_slots = _time_slots(open_time, close_time, duration_min)
    available = [s for s in all_slots if s not in occupied]

    return {
        "facility_id": facility_id,
        "date": date,
        "open_time": open_time,
        "close_time": close_time,
        "default_duration_min": duration_min,
        "available_slots": available,
    }


async def create_booking(
    adapter: Any,
    facility_id: str,
    date: str,
    start_time: str,
    end_time: str,
    user_email: str,
) -> dict:
    return await adapter.make_booking(facility_id, date, start_time, end_time, user_email)


async def cancel_booking(adapter: Any, booking_id: str) -> dict:
    return await adapter.cancel_booking(booking_id)


async def get_my_bookings(adapter: Any) -> dict:
    return await adapter.get_my_bookings()
