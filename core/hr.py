"""Leave balance aur working days.

Working day = Somvaar se Shukravaar, jo office holiday na ho. (Saturday/Sunday off maane gaye hain.)
"""
import calendar
import datetime

from .models import Holiday, LeavePolicy, LeaveRequest

# Policy row na ho to ye default chalta hai. days = 0 ka matlab quota nahi.
DEFAULT_POLICY = {
    'annual': (18, True),
    'sick': (8, True),
    'casual': (6, True),
    'unpaid': (0, False),
    'other': (0, True),
}


def policies():
    """{kind: (days_per_year, is_paid)}: DB mein jo set hai wo, baaki default."""
    result = dict(DEFAULT_POLICY)
    for row in LeavePolicy.objects.all():
        result[row.kind] = (row.days_per_year, row.is_paid)
    return result


def holiday_set(start, end):
    return set(Holiday.objects.filter(date__range=(start, end)).values_list('date', flat=True))


def working_days(start, end, holidays=None):
    """start se end (dono shamil) ke beech ke working days."""
    if end < start:
        return 0
    if holidays is None:
        holidays = holiday_set(start, end)
    count = 0
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in holidays:
            count += 1
        day += datetime.timedelta(days=1)
    return count


def year_bounds(year):
    return datetime.date(year, 1, 1), datetime.date(year, 12, 31)


def month_bounds(year, month):
    return datetime.date(year, month, 1), datetime.date(year, month, calendar.monthrange(year, month)[1])


def _clipped_days(leave, start, end, holidays):
    low, high = max(leave.start_date, start), min(leave.end_date, end)
    return working_days(low, high, holidays) if low <= high else 0


def leave_balance(user, year, policy=None):
    """Har leave type ke liye: quota (None = unlimited), used, pending, remaining."""
    policy = policy or policies()
    start, end = year_bounds(year)
    holidays = holiday_set(start, end)
    leaves = LeaveRequest.objects.filter(
        user=user, status__in=('approved', 'pending'), start_date__lte=end, end_date__gte=start,
    )
    used = {kind: 0 for kind, _ in LeaveRequest.KIND_CHOICES}
    pending = dict(used)
    for leave in leaves:
        days = _clipped_days(leave, start, end, holidays)
        (used if leave.status == 'approved' else pending)[leave.kind] += days

    rows = []
    for kind, label in LeaveRequest.KIND_CHOICES:
        quota, paid = policy.get(kind, (0, True))
        rows.append({
            'kind': kind,
            'label': label,
            'quota': quota or None,
            'paid': paid,
            'used': used[kind],
            'pending': pending[kind],
            'remaining': (quota - used[kind] - pending[kind]) if quota else None,
        })
    return rows


def check_leave_request(user, kind, start, end):
    """Naye leave request ki galti ka message, ya None agar sab theek hai."""
    holidays = holiday_set(start, end)
    if working_days(start, end, holidays) < 1:
        return 'These dates are all weekends or holidays. Pick at least one working day.'

    quota, _paid = policies().get(kind, (0, True))
    if not quota:
        return None
    for year in range(start.year, end.year + 1):
        year_start, year_end = year_bounds(year)
        asked = working_days(max(start, year_start), min(end, year_end))
        if not asked:
            continue
        row = next(r for r in leave_balance(user, year) if r['kind'] == kind)
        if asked > row['remaining']:
            label = dict(LeaveRequest.KIND_CHOICES)[kind].lower()
            left = max(row['remaining'], 0)
            return f'You asked for {asked} working day{"s" if asked != 1 else ""} of {label} in {year} but only {left} {"is" if left == 1 else "are"} left.'
    return None


def unpaid_days_in_month(user, year, month, policy=None):
    """Us mahine ke approved unpaid-leave working days."""
    policy = policy or policies()
    unpaid_kinds = [kind for kind, (_days, paid) in policy.items() if not paid]
    start, end = month_bounds(year, month)
    holidays = holiday_set(start, end)
    leaves = LeaveRequest.objects.filter(
        user=user, status='approved', kind__in=unpaid_kinds, start_date__lte=end, end_date__gte=start,
    )
    return sum(_clipped_days(leave, start, end, holidays) for leave in leaves)
