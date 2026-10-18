"""Budget ka hisaab.

Spent = approved expenses + labour cost.
Labour cost = logged time x us user ka hourly rate (UserProfile.hourly_rate).
Waiting-for-review expenses alag dikhte hain aur spent mein nahi ginte.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth.models import User
from django.db.models import Q, Sum
from django.urls import reverse

from .models import Expense, Task, TimeEntry, UserProfile
from .services import notify_many

CENT = Decimal('0.01')
ZERO = Decimal('0')
WARN_LEVEL = 80


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def hourly_rates():
    """{user_id: rate}. Ek baar nikalo aur kai projects ke liye reuse karo."""
    return dict(UserProfile.objects.exclude(hourly_rate=None).values_list('user_id', 'hourly_rate'))


def project_budget_summary(project, rates=None):
    if rates is None:
        rates = hourly_rates()

    approved_qs = project.expenses.filter(status='approved')
    approved = approved_qs.aggregate(total=Sum('amount'))['total'] or ZERO
    pending = project.expenses.filter(status='submitted').aggregate(total=Sum('amount'))['total'] or ZERO

    categories = [
        {'label': dict(Expense.CATEGORY_CHOICES).get(row['category'], row['category']), 'total': row['total']}
        for row in approved_qs.order_by().values('category').annotate(total=Sum('amount')).order_by('-total')
    ]

    labor_rows = []
    labor_cost = ZERO
    unrated_minutes = 0
    time_rows = (
        TimeEntry.objects.filter(task__project=project).order_by()
        .values('user_id', 'user__username').annotate(minutes=Sum('minutes')).order_by('user__username')
    )
    logged_minutes = 0
    for row in time_rows:
        minutes = row['minutes']
        logged_minutes += minutes
        rate = rates.get(row['user_id'])
        hours = Decimal(minutes) / Decimal(60)
        if rate is None:
            unrated_minutes += minutes
            cost = None
        else:
            cost = money(hours * rate)
            labor_cost += cost
        labor_rows.append({
            'username': row['user__username'] or 'Deleted user',
            'minutes': minutes,
            'hours': hours.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP),
            'rate': rate,
            'cost': cost,
        })

    spent = approved + labor_cost
    budget = project.budget
    has_budget = budget is not None and budget > 0

    percent = None
    level = 0
    state = 'none'
    remaining = None
    over_by = ZERO
    if has_budget:
        percent = int((spent * 100 / budget).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        remaining = budget - spent
        over_by = spent - budget if spent > budget else ZERO
        if spent >= budget:
            level = 100
        elif spent * 100 >= budget * WARN_LEVEL:
            level = WARN_LEVEL
        state = 'over' if spent > budget else ('warning' if level >= WARN_LEVEL else 'ok')

    estimated = Task.objects.filter(project=project).aggregate(total=Sum('estimated_hours'))['total'] or ZERO

    return {
        'budget': budget if has_budget else None,
        'has_budget': has_budget,
        'expenses_approved': approved,
        'expenses_pending': pending,
        'labor_cost': labor_cost,
        'labor_rows': labor_rows,
        'unrated_minutes': unrated_minutes,
        'unrated_hours': (Decimal(unrated_minutes) / Decimal(60)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP),
        'spent': spent,
        'remaining': remaining,
        'over_by': over_by,
        'percent': percent,
        'bar': min(percent, 100) if percent is not None else 0,
        'level': level,
        'state': state,
        'categories': categories,
        'estimated_hours': estimated,
        'logged_hours': (Decimal(logged_minutes) / Decimal(60)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP),
    }


def budget_managers():
    """Budget alert inhi ko jata hai: admin aur managers."""
    return list(
        User.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(profile__role__in=['admin', 'manager']))
        .distinct()
    )


def refresh_budget_alert(project, request=None):
    """Budget level (80% ya 100%) cross hone par managers ko ek baar notify karta hai.

    Idempotent: kitni baar bhi call karo, ek level ka alert sirf ek baar jata hai.
    Budget badhane ya kharcha hatane se level neeche aaye to level reset hota hai, taaki aage phir alert aa sake.
    """
    if project is None:
        return None

    project.refresh_from_db(fields=['budget', 'budget_alerted_level'])
    summary = project_budget_summary(project)
    level = summary['level']
    previous = project.budget_alerted_level

    if level > previous:
        if summary['over_by'] > 0:
            message = f'Budget exceeded: "{project.name}" is over budget by {money(summary["over_by"])}'
        elif level >= 100:
            message = f'Budget fully used: "{project.name}" has no budget left'
        else:
            message = f'Budget alert: "{project.name}" has used {summary["percent"]}% of its budget'
        notify_many(
            budget_managers(), message,
            link=reverse('project_budget', args=[project.pk]), request=request,
        )

    if level != previous:
        type(project).objects.filter(pk=project.pk).update(budget_alerted_level=level)
        project.budget_alerted_level = level
    return summary
