"""Reports (CSV export), analytics aur calendar (.ics) export."""
import csv
import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .filters import scope_tasks
from .models import Task, TimeEntry
from .views import format_minutes

WEEKS_SHOWN = 8


def _csv_safe(value):
    """Excel/Sheets formula injection se bachao: '=', '+', '-', '@' se shuru hone wale text ke aage ' lagao."""
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


@login_required
def task_report(request):
    tasks = Task.objects.select_related('assigned_to', 'project').prefetch_related('labels')
    status_filter = request.GET.get('status')
    # Sirf valid status hi filter hone do; galat value par saare tasks dikhao
    if status_filter in dict(Task.STATUS_CHOICES):
        tasks = tasks.filter(status=status_filter)
    else:
        status_filter = None

    if request.GET.get('export') == 'csv':
        return _csv_response(tasks, status_filter)

    context = {
        'tasks': tasks,
        'status_filter': status_filter,
        'status_choices': Task.STATUS_CHOICES,
    }
    return render(request, 'core/report.html', context)


def _csv_response(tasks, status_filter):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    name = 'tasks-' + (status_filter or 'all').lower().replace(' ', '-') + '.csv'
    response['Content-Disposition'] = f'attachment; filename="{name}"'
    response.write('\ufeff')  # Excel mein UTF-8 sahi khulne ke liye

    writer = csv.writer(response)
    writer.writerow(['ID', 'Title', 'Project', 'Assigned to', 'Status', 'Priority', 'Progress %', 'Due date', 'Labels', 'Created'])
    for task in tasks:
        writer.writerow([
            task.id,
            _csv_safe(task.title),
            _csv_safe(task.project.name if task.project_id else ''),
            _csv_safe(task.assigned_to.username),
            task.status,
            task.priority,
            task.progress,
            task.due_date.isoformat() if task.due_date else '',
            _csv_safe(', '.join(label.name for label in task.labels.all())),
            timezone.localtime(task.created_at).strftime('%Y-%m-%d %H:%M'),
        ])
    return response


def _week_start(day):
    return day - datetime.timedelta(days=day.weekday())


@login_required
def analytics(request):
    today = timezone.localdate()
    tasks = Task.objects.all()

    # Per-person workload
    people = list(
        tasks.order_by().values('assigned_to__username').annotate(
            total=Count('id'),
            done=Count('id', filter=Q(status='Completed')),
            open=Count('id', filter=~Q(status='Completed')),
            overdue=Count('id', filter=Q(due_date__lt=today) & ~Q(status='Completed')),
        ).order_by('-open', 'assigned_to__username')
    )
    minutes_by_user = {
        row['user__username']: row['minutes']
        for row in TimeEntry.objects.order_by().values('user__username').annotate(minutes=Sum('minutes'))
    }
    max_open = max([p['open'] for p in people], default=0)
    for person in people:
        person['username'] = person['assigned_to__username']
        person['bar'] = round(person['open'] * 100 / max_open) if max_open else 0
        person['time'] = format_minutes(minutes_by_user.get(person['username'], 0))

    # Pichhle 8 hafte: banaye gaye vs complete hue
    this_week = _week_start(today)
    starts = [this_week - datetime.timedelta(weeks=i) for i in range(WEEKS_SHOWN - 1, -1, -1)]
    created = {start: 0 for start in starts}
    completed = {start: 0 for start in starts}
    window_start = timezone.make_aware(datetime.datetime.combine(starts[0], datetime.time.min))

    for stamp in tasks.filter(created_at__gte=window_start).values_list('created_at', flat=True):
        key = _week_start(timezone.localtime(stamp).date())
        if key in created:
            created[key] += 1
    for stamp in tasks.filter(completed_at__gte=window_start).values_list('completed_at', flat=True):
        key = _week_start(timezone.localtime(stamp).date())
        if key in completed:
            completed[key] += 1

    peak = max(list(created.values()) + list(completed.values()) + [0])
    weekly = [
        {
            'label': start.strftime('%d %b'),
            'created': created[start],
            'completed': completed[start],
            'created_pct': round(created[start] * 100 / peak) if peak else 0,
            'completed_pct': round(completed[start] * 100 / peak) if peak else 0,
        }
        for start in starts
    ]

    # Average completion time
    durations = [
        done - made
        for made, done in tasks.filter(status='Completed', completed_at__isnull=False).values_list('created_at', 'completed_at')
        if done >= made
    ]
    average_completion = None
    if durations:
        seconds = sum(d.total_seconds() for d in durations) / len(durations)
        days, remainder = divmod(int(seconds), 86400)
        hours = remainder // 3600
        average_completion = f'{days}d {hours}h' if days else f'{hours}h'

    by_status = dict(tasks.order_by().values_list('status').annotate(total=Count('id')))
    by_priority = dict(tasks.order_by().values_list('priority').annotate(total=Count('id')))
    total_minutes = TimeEntry.objects.aggregate(total=Sum('minutes'))['total'] or 0

    return render(request, 'core/analytics.html', {
        'people': people,
        'weekly': weekly,
        'average_completion': average_completion,
        'completed_count': len(durations),
        'total_tasks': tasks.count(),
        'overdue_total': tasks.filter(due_date__lt=today).exclude(status='Completed').count(),
        'total_time': format_minutes(total_minutes),
        'status_rows': [(label, by_status.get(status, 0), Task.STATUS_CSS[status]) for status, label in Task.STATUS_CHOICES],
        'priority_rows': [(label, by_priority.get(value, 0), value.lower()) for value, label in Task.PRIORITY_CHOICES],
    })


# ---------- calendar export (.ics) ----------

def _ics_escape(text):
    return (
        text.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
        .replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', '\\n')
    )


def _ics_fold(line):
    """RFC 5545: lines 75 bytes se lambi na hon; agli line space se shuru hoti hai."""
    if len(line.encode('utf-8')) <= 75:
        return line
    chunks, current, limit = [], '', 75
    for char in line:
        if len((current + char).encode('utf-8')) > limit:
            chunks.append(current)
            current, limit = char, 74
        else:
            current += char
    chunks.append(current)
    return '\r\n '.join(chunks)


@login_required
def calendar_ics(request):
    """Due date wale pending tasks ko .ics file mein deta hai (Google/Outlook/Apple Calendar mein import)."""
    scope = scope_tasks(
        Task.objects.select_related('assigned_to', 'project'), request.GET, request.user,
    ).filter(due_date__isnull=False).exclude(status='Completed').order_by('due_date', 'id')

    stamp = timezone.now().strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Taskboard//Tasks//EN', 'CALSCALE:GREGORIAN', 'X-WR-CALNAME:Taskboard']
    for task in scope:
        description = f'Assigned to {task.assigned_to.username}. Priority {task.priority}. Status {task.status}.'
        lines += [
            'BEGIN:VEVENT',
            f'UID:task-{task.pk}@taskboard',
            f'DTSTAMP:{stamp}',
            f'DTSTART;VALUE=DATE:{task.due_date.strftime("%Y%m%d")}',
            f'DTEND;VALUE=DATE:{(task.due_date + datetime.timedelta(days=1)).strftime("%Y%m%d")}',
            f'SUMMARY:{_ics_escape(task.title)}',
            f'DESCRIPTION:{_ics_escape(description)}',
            'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')

    body = '\r\n'.join(_ics_fold(line) for line in lines) + '\r\n'
    response = HttpResponse(body, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="tasks.ics"'
    return response
