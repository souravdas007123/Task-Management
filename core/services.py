"""Chhote helpers: activity log, notifications (in-app + email), status change, task snapshot/diff."""
import re
from decimal import Decimal

from django.contrib.auth.models import User

from django.core.mail import send_mail

from .models import ActivityLog, Notification, Task
from .permissions import can_edit_task, can_view_task


class BlockedError(ValueError):
    """Task complete nahi ho sakta kyunki uske blocker tasks abhi baaki hain."""


def blocked_message(task):
    ids = ', '.join(f'#{pk}' for pk in task.open_blockers().values_list('pk', flat=True)[:5])
    return f'Finish the blocking tasks first: {ids}.'


def display_name(user):
    if user is None:
        return 'Someone'
    return user.get_full_name() or user.username


def log_activity(task, actor, message):
    return ActivityLog.objects.create(task=task, actor=actor, message=message[:300])


def notify(user, message, link='', actor=None, request=None):
    """In-app notification banata hai aur (agar user ka email hai) email bhejta hai.

    Khud ko notify nahi karta. Email fail ho to app nahi rukta (fail_silently).
    """
    if user is None or not user.is_active:
        return None
    if actor is not None and user.pk == actor.pk:
        return None

    notification = Notification.objects.create(user=user, message=message[:300], link=link[:200])

    if user.email:
        body = message
        if link:
            body += '\n\n' + (request.build_absolute_uri(link) if request is not None else link)
        subject = 'Taskboard: ' + ' '.join(message.split())[:90]
        send_mail(subject, body, None, [user.email], fail_silently=True)
    return notification


def notify_many(users, message, link='', actor=None, request=None):
    seen = set()
    for user in users:
        if user is None or user.pk in seen:
            continue
        seen.add(user.pk)
        notify(user, message, link=link, actor=actor, request=request)


MENTION_RE = re.compile(r'(?<![\w@.])@([\w.+-]+)')
MAX_MENTIONS = 10


def find_mentions(text, task):
    """Comment mein @username likhe active users, jo is task ko dekh sakte hain (max 10)."""
    names = []
    for raw in MENTION_RE.findall(text or ''):
        name = raw.rstrip('.+-')
        if name and name.lower() not in [n.lower() for n in names]:
            names.append(name)
        if len(names) >= MAX_MENTIONS:
            break
    if not names:
        return []
    found = {}
    for user in User.objects.filter(is_active=True):
        if user.username.lower() in {n.lower() for n in names}:
            found[user.pk] = user
    return [user for user in found.values() if can_view_task(user, task)]


# ---------- change tracking ----------

TRACKED_FIELDS = [
    ('title', 'Title'),
    ('assigned_to', 'Assigned to'),
    ('project', 'Project'),
    ('status', 'Status'),
    ('priority', 'Priority'),
    ('progress', 'Progress'),
    ('start_date', 'Start date'),
    ('due_date', 'Due date'),
    ('estimated_hours', 'Estimate'),
    ('recurrence', 'Repeat'),
    ('labels', 'Labels'),
    ('blocked_by', 'Blocked by'),
    ('description', 'Description'),
]


def snapshot_task(task):
    """Edit se pehle/baad ki values ko readable strings mein."""
    return {
        'title': task.title,
        'assigned_to': task.assigned_to.username,
        'project': task.project.name if task.project_id else '-',
        'status': task.status,
        'priority': task.priority,
        'progress': f'{task.progress}%',
        'start_date': task.start_date.isoformat() if task.start_date else '-',
        'due_date': task.due_date.isoformat() if task.due_date else '-',
        'estimated_hours': f'{Decimal(task.estimated_hours).normalize():f}h' if task.estimated_hours is not None else '-',
        'recurrence': task.get_recurrence_display(),
        'labels': ', '.join(sorted(label.name for label in task.labels.all())) or '-',
        'blocked_by': ', '.join(f'#{pk}' for pk in sorted(task.blocked_by.values_list('pk', flat=True))) or '-',
        'description': task.description,
    }


def diff_snapshots(before, after):
    """[(key, label, old, new)] sirf unke liye jo badle."""
    return [
        (key, label, before[key], after[key])
        for key, label in TRACKED_FIELDS
        if before[key] != after[key]
    ]


def record_task_changes(task, actor, changes, request=None):
    """Har change ka activity log aur zaroori notifications."""
    link = task.get_absolute_url()
    who = display_name(actor)

    for key, label, old, new in changes:
        if key == 'description':
            log_activity(task, actor, 'Edited the description')
        else:
            log_activity(task, actor, f'Changed {label.lower()} from "{old}" to "{new}"')

    changed_keys = {key for key, *_ in changes}

    if 'assigned_to' in changed_keys:
        notify(task.assigned_to, f'{who} assigned you "{task.title}"', link=link, actor=actor, request=request)

    if 'status' in changed_keys:
        new_status = task.status
        notify_many(
            [task.assigned_to, task.created_by],
            f'{who} changed "{task.title}" to {new_status}',
            link=link, actor=actor, request=request,
        )


def change_status(task, new_status, actor, request=None):
    """Board/API se status badalna: validate, save, log, notify. Purana status return karta hai."""
    if new_status not in dict(Task.STATUS_CHOICES):
        raise ValueError('Invalid status')

    old_status = task.status
    if new_status == old_status:
        return old_status
    if new_status == 'Completed' and task.open_blockers().exists():
        raise BlockedError(blocked_message(task))

    task.status = new_status
    task.save(update_fields=['status'])
    record_task_changes(task, actor, [('status', 'Status', old_status, new_status)], request=request)
    return old_status


def annotate_permissions(user, tasks):
    """Har task par can_edit flag lagata hai (template mein buttons dikhane ke liye)."""
    for task in tasks:
        task.can_edit = can_edit_task(user, task)
    return tasks
