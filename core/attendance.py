"""Attendance ka hisab: late, half day, absent, mahine ka summary.

Working day = Somvaar-Shukravaar jo office holiday na ho. Approved leave wale din absent nahi ginte.
"""
import datetime

from django.conf import settings
from django.utils import timezone

from .hr import holiday_set, month_bounds
from .models import Attendance, LeaveRequest

DAY_LABELS = {
    'present': 'Present', 'half': 'Half day', 'short': 'Short day', 'open': 'Working now',
    'no_checkout': 'No check-out', 'leave': 'On leave', 'absent': 'Absent', 'holiday': 'Holiday',
    'weekend': 'Weekend', 'future': '', 'before': 'Before joining',
}


def work_start():
    """settings se office shuru hone ka time; galat likha ho to 09:30."""
    try:
        hour, minute = (int(part) for part in str(settings.ATTENDANCE_START).split(':'))
        return datetime.time(hour, minute)
    except (ValueError, TypeError):
        return datetime.time(9, 30)


def late_cutoff():
    start = datetime.datetime.combine(datetime.date(2000, 1, 1), work_start())
    return (start + datetime.timedelta(minutes=settings.ATTENDANCE_GRACE_MINUTES)).time()


def is_late(record):
    return timezone.localtime(record.check_in).time() > late_cutoff()


def classify(record, today):
    """Ek record ka status: present / half / short / open / no_checkout."""
    if record.check_out is None:
        return 'open' if record.date >= today else 'no_checkout'
    hours = record.hours
    if hours >= settings.ATTENDANCE_FULL_DAY_HOURS:
        return 'present'
    if hours >= settings.ATTENDANCE_HALF_DAY_HOURS:
        return 'half'
    return 'short'


WEIGHT = {'present': 1.0, 'half': 0.5, 'short': 0.0, 'open': 1.0, 'no_checkout': 1.0}


def _leave_dates(leaves, start, end):
    dates = set()
    for leave in leaves:
        day = max(leave.start_date, start)
        stop = min(leave.end_date, end)
        while day <= stop:
            dates.add(day)
            day += datetime.timedelta(days=1)
    return dates


def analyze(records, leaves, holidays, year, month, today, joined=None):
    """Ek bande ke mahine ke rows aur summary.

    records: us mahine ke Attendance; leaves: approved LeaveRequest jo mahine ko chhoote hain.
    Absent sirf beete hue (aaj se pehle ke) working days ke liye ginte hain.
    """
    first, last = month_bounds(year, month)
    by_date = {r.date: r for r in records}
    on_leave = _leave_dates(leaves, first, last)

    rows = []
    summary = {'present': 0.0, 'late': 0, 'absent': 0, 'leave': 0, 'half': 0, 'no_checkout': 0, 'hours': 0.0, 'days_marked': 0}
    day = first
    while day <= min(last, today):
        record = by_date.get(day)
        working = day.weekday() < 5 and day not in holidays
        row = {'date': day, 'record': record, 'late': False, 'hours': None}
        if joined and day < joined and not record:
            row['status'] = 'before'
        elif record:
            status = classify(record, today)
            row.update(status=status, late=is_late(record), hours=record.hours)
            summary['present'] += WEIGHT[status]
            summary['days_marked'] += 1
            summary['late'] += 1 if row['late'] else 0
            summary['half'] += 1 if status == 'half' else 0
            summary['no_checkout'] += 1 if status == 'no_checkout' else 0
            summary['hours'] += record.hours or 0
        elif not working:
            row['status'] = 'holiday' if (day.weekday() < 5) else 'weekend'
        elif day in on_leave:
            row['status'] = 'leave'
            summary['leave'] += 1
        elif day < today:
            row['status'] = 'absent'
            summary['absent'] += 1
        else:
            row['status'] = 'future'  # aaj abhi check-in nahi hua
        row['label'] = DAY_LABELS.get(row['status'], '')
        rows.append(row)
        day += datetime.timedelta(days=1)
    summary['present'] = round(summary['present'], 1)
    summary['hours'] = round(summary['hours'], 1)
    return rows, summary


def month_data(users, year, month, today=None):
    """Kai users ke liye ek saath (kam queries): {user_id: (rows, summary)}."""
    today = today or timezone.localdate()
    first, last = month_bounds(year, month)
    user_ids = [u.pk for u in users]
    holidays = holiday_set(first, last)

    records, leaves = {}, {}
    for record in Attendance.objects.filter(user_id__in=user_ids, date__range=(first, last)):
        records.setdefault(record.user_id, []).append(record)
    for leave in LeaveRequest.objects.filter(
        user_id__in=user_ids, status='approved', start_date__lte=last, end_date__gte=first,
    ):
        leaves.setdefault(leave.user_id, []).append(leave)

    result = {}
    for user in users:
        profile = getattr(user, 'profile', None)
        joined = profile.joining_date if profile else None
        result[user.pk] = analyze(
            records.get(user.pk, []), leaves.get(user.pk, []), holidays, year, month, today, joined,
        )
    return result
