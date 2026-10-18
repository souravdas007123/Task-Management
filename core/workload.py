"""Work management: kis par kitna kaam hai (workload) aur tasks ki timeline."""
import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth.models import User
from django.db.models import Sum
from django.utils import timezone

from .models import Task, TimeEntry, UserProfile

DEFAULT_CAPACITY = 40
DUE_SOON_DAYS = 7
HIGH_LOAD = 80  # percent
TIMELINE_WEEKS = 8


def remaining_hours(estimated_hours, progress):
    """Estimate ka bacha hua hissa: 10h ka task jo 40% ho chuka hai, uske 6h bache."""
    if estimated_hours is None:
        return Decimal('0')
    return Decimal(estimated_hours) * (100 - progress) / Decimal(100)


def one_decimal(value):
    return Decimal(value).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def week_start(day):
    return day - datetime.timedelta(days=day.weekday())


def build_workload(today=None):
    """Har active user ke liye: open tasks, bacha hua estimate, is hafte ka load vs capacity."""
    today = today or timezone.localdate()
    soon = today + datetime.timedelta(days=DUE_SOON_DAYS)
    this_week = week_start(today)

    capacities = dict(UserProfile.objects.values_list('user_id', 'weekly_capacity_hours'))

    rows = {}
    for user in User.objects.filter(is_active=True).order_by('username'):
        rows[user.pk] = {
            'username': user.username,
            'capacity': capacities.get(user.pk, DEFAULT_CAPACITY),
            'open_tasks': 0,
            'unestimated': 0,
            'overdue': 0,
            'remaining': Decimal('0'),
            'due_soon': Decimal('0'),
        }

    open_tasks = Task.objects.exclude(status='Completed').values_list(
        'assigned_to_id', 'estimated_hours', 'progress', 'due_date',
    )
    for user_id, estimate, progress, due_date in open_tasks:
        row = rows.get(user_id)
        if row is None:  # inactive user ke tasks yahan nahi dikhte
            continue
        row['open_tasks'] += 1
        if estimate is None:
            row['unestimated'] += 1
        left = remaining_hours(estimate, progress)
        row['remaining'] += left
        if due_date is not None:
            if due_date < today:
                row['overdue'] += 1
            if due_date <= soon:
                row['due_soon'] += left

    logged = {
        row['user_id']: row['minutes']
        for row in TimeEntry.objects.filter(worked_on__range=(this_week, this_week + datetime.timedelta(days=6)))
        .order_by().values('user_id').annotate(minutes=Sum('minutes'))
    }

    result = []
    for user_id, row in rows.items():
        utilization = int((row['due_soon'] * 100 / row['capacity']).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        level = 'over' if utilization > 100 else ('high' if utilization >= HIGH_LOAD else 'ok')
        result.append({
            **row,
            'remaining': one_decimal(row['remaining']),
            'due_soon': one_decimal(row['due_soon']),
            'utilization': utilization,
            'bar': min(utilization, 100),
            'level': level,
            'logged_week': one_decimal(Decimal(logged.get(user_id, 0)) / 60),
        })
    result.sort(key=lambda r: (-r['utilization'], -r['open_tasks'], r['username']))
    return result


def build_timeline(tasks, window_start, weeks=TIMELINE_WEEKS, today=None):
    """Gantt jaisi rows. Bar start_date se due_date tak; start_date na ho to due date par ek din ka marker."""
    today = today or timezone.localdate()
    total_days = weeks * 7
    window_end = window_start + datetime.timedelta(days=total_days - 1)

    rows = []
    undated = 0
    for task in tasks:
        if not task.due_date:
            undated += 1
            continue
        end = task.due_date
        start = min(task.start_date, end) if task.start_date else end
        if end < window_start or start > window_end:
            continue
        shown_start = max(start, window_start)
        shown_end = min(end, window_end)
        rows.append({
            'task': task,
            'start': start,
            'end': end,
            'is_marker': start == end,
            'left': round((shown_start - window_start).days * 100 / total_days, 2),
            'width': round(((shown_end - shown_start).days + 1) * 100 / total_days, 2),
            'cut_left': start < window_start,
            'cut_right': end > window_end,
        })

    header = [
        {
            'label': (window_start + datetime.timedelta(weeks=i)).strftime('%d %b'),
            'left': round(i * 100 / weeks, 2),
        }
        for i in range(weeks)
    ]
    today_left = None
    if window_start <= today <= window_end:
        today_left = round(((today - window_start).days + 0.5) * 100 / total_days, 2)

    return {
        'rows': rows,
        'undated': undated,
        'header': header,
        'today_left': today_left,
        'window_start': window_start,
        'window_end': window_end,
    }
