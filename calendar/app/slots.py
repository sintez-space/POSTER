from datetime import datetime, timedelta

SLOT_HOURS = (9, 11, 13, 15, 17, 19)
SLOT_LABELS = ("09–11", "11–13", "13–15", "15–17", "17–19", "19–21")


def monday_of(d: datetime | None = None) -> datetime:
    d = d or datetime.now()
    return (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def validate_scheduled_at(value: str, disallow_past: bool = True) -> str:
    raw = (value or "").strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("неверная дата и время") from exc
    if dt.weekday() > 4:
        raise ValueError("публикации только с понедельника по пятницу")
    if dt.hour not in SLOT_HOURS or dt.minute != 0:
        raise ValueError("слот: 09:00, 11:00, 13:00, 15:00, 17:00 или 19:00")
    if disallow_past:
        now_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if dt < now_day:
            raise ValueError("нельзя ставить публикацию на прошедшую дату")
    return dt.strftime("%Y-%m-%dT%H:%M")


def week_range(week_start: str):
    try:
        mon = datetime.fromisoformat(week_start.strip()).date()
    except ValueError as exc:
        raise ValueError("week_start: дата YYYY-MM-DD") from exc
    if mon.weekday() != 0:
        raise ValueError("week_start должен быть понедельником")
    fri = mon + timedelta(days=4)
    return mon.isoformat(), fri.isoformat() + "T23:59"


def date_span(date_from: str, date_to: str):
    try:
        start = datetime.fromisoformat(date_from.strip()).date()
        end = datetime.fromisoformat(date_to.strip()).date()
    except ValueError as exc:
        raise ValueError("дата: YYYY-MM-DD") from exc
    if end < start:
        raise ValueError("date_to раньше date_from")
    if (end - start).days > 21:
        raise ValueError("слишком длинный диапазон")
    return start.isoformat(), end.isoformat() + "T23:59"
