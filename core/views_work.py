"""Work management: workload (kis par kitna kaam) aur timeline (Gantt jaisa view)."""
import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from .filters import parse_int, scope_tasks
from .models import Project, Task
from .permissions import visible_projects
from .views_office import away_user_ids
from .workload import (
    DUE_SOON_DAYS, HIGH_LOAD, TIMELINE_WEEKS, build_timeline, build_workload, week_start,
)

TIMELINE_LIMIT = 300
SHIFT_WEEKS = 4


@login_required
def workload(request):
    today = timezone.localdate()
    rows = build_workload(today)
    away_ids = away_user_ids(today)
    for row in rows:
        row['away'] = row['user_id'] in away_ids
    return render(request, 'core/workload.html', {
        'rows': rows,
        'due_soon_days': DUE_SOON_DAYS,
        'high_load': HIGH_LOAD,
        'week_start': week_start(today),
    })


def _parse_date(value):
    try:
        day = datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return day if 1900 <= day.year <= 2100 else None


@login_required
def timeline(request):
    today = timezone.localdate()
    requested = _parse_date(request.GET.get('start'))
    # Default: pichhla hafta + is hafte se aage ke hafte
    window_start = week_start(requested) if requested else week_start(today) - datetime.timedelta(weeks=1)
    window_end = window_start + datetime.timedelta(weeks=TIMELINE_WEEKS) - datetime.timedelta(days=1)

    scope = scope_tasks(Task.objects.select_related('assigned_to', 'project'), request.GET, request.user)
    undated = scope.filter(due_date__isnull=True).count()
    tasks = list(
        scope.filter(due_date__gte=window_start)
        .filter(Q(start_date__lte=window_end) | Q(start_date__isnull=True, due_date__lte=window_end))
        .order_by('start_date', 'due_date', 'id')[:TIMELINE_LIMIT]
    )

    data = build_timeline(tasks, window_start, today=today)
    data['undated'] = undated

    shift = datetime.timedelta(weeks=SHIFT_WEEKS)
    return render(request, 'core/timeline.html', {
        **data,
        'prev_start': (window_start - shift).isoformat(),
        'next_start': (window_start + shift).isoformat(),
        'mine': request.GET.get('mine') == '1',
        'projects': visible_projects(Project.objects.all(), request.user),
        'current_project_id': parse_int(request.GET.get('project')),
        'truncated': len(tasks) >= TIMELINE_LIMIT,
        'limit': TIMELINE_LIMIT,
        'weeks': TIMELINE_WEEKS,
    })
