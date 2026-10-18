"""Board (kanban), calendar aur notifications."""
import calendar
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .filters import parse_int, scope_tasks, sort_tasks
from .models import Notification, Project, Task
from .services import annotate_permissions

BOARD_LIMIT = 300


@login_required
def board(request):
    scope = scope_tasks(
        Task.objects.select_related('assigned_to', 'project').prefetch_related('labels'),
        request.GET, request.user,
    )
    total = scope.count()
    tasks = annotate_permissions(request.user, list(sort_tasks(scope, 'priority')[:BOARD_LIMIT]))

    columns = [
        {
            'status': status,
            'label': label,
            'css': Task.STATUS_CSS[status],
            'tasks': [task for task in tasks if task.status == status],
        }
        for status, label in Task.STATUS_CHOICES
    ]
    return render(request, 'core/board.html', {
        'columns': columns,
        'status_choices': Task.STATUS_CHOICES,
        'truncated': total > BOARD_LIMIT,
        'board_limit': BOARD_LIMIT,
        'mine': request.GET.get('mine') == '1',
        'projects': Project.objects.all(),
        'current_project_id': parse_int(request.GET.get('project')),
    })


def _month_from_request(request):
    today = timezone.localdate()
    year = parse_int(request.GET.get('year'))
    month = parse_int(request.GET.get('month'))
    if not year or not month or not (1900 <= year <= 2100) or not (1 <= month <= 12):
        return today.year, today.month
    return year, month


@login_required
def task_calendar(request):
    year, month = _month_from_request(request)
    today = timezone.localdate()

    # Sunday se shuru hone wala hafta
    weeks_dates = calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)
    first_day, last_day = weeks_dates[0][0], weeks_dates[-1][-1]

    scope = scope_tasks(Task.objects.select_related('assigned_to'), request.GET, request.user)
    by_date = {}
    for task in scope.filter(due_date__range=(first_day, last_day)).order_by('due_date', 'id'):
        by_date.setdefault(task.due_date, []).append(task)

    weeks = []
    for week in weeks_dates:
        cells = []
        for day in week:
            day_tasks = by_date.get(day, [])
            cells.append({
                'date': day,
                'in_month': day.month == month,
                'is_today': day == today,
                'tasks': day_tasks[:3],
                'extra': max(len(day_tasks) - 3, 0),
            })
        weeks.append(cells)

    previous_month = datetime.date(year, month, 1) - datetime.timedelta(days=1)
    next_month = datetime.date(year, month, 28) + datetime.timedelta(days=5)

    return render(request, 'core/calendar.html', {
        'weeks': weeks,
        'month_name': calendar.month_name[month],
        'year': year,
        'month': month,
        'weekday_names': ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
        'prev': {'year': previous_month.year, 'month': previous_month.month},
        'next': {'year': next_month.year, 'month': next_month.month},
        'mine': request.GET.get('mine') == '1',
        'today': today,
    })


@login_required
def notifications(request):
    items = Notification.objects.filter(user=request.user)[:50]
    return render(request, 'core/notifications.html', {'notifications': items})


@login_required
@require_POST
def notifications_mark_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    messages.success(request, 'All notifications marked as read.')
    return redirect('notifications')


@login_required
def notification_open(request, pk):
    """Notification ko read mark karke uske link par bhejta hai."""
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read'])
    link = notification.link
    if link and url_has_allowed_host_and_scheme(link, allowed_hosts=None):
        return redirect(link)
    return redirect(reverse('notifications'))
