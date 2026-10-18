"""Task list ke liye search, filter aur sort (dashboard aur API dono use karte hain)."""
from django.db.models import Case, F, IntegerField, Q, When
from django.utils import timezone

from .models import Task
from .permissions import visible_tasks

SORT_CHOICES = [
    ('newest', 'Newest first'),
    ('oldest', 'Oldest first'),
    ('due', 'Due date'),
    ('priority', 'Priority'),
    ('progress', 'Progress, high to low'),
    ('title', 'Title A to Z'),
]


def parse_int(value):
    """URL ke param se safe int; galat ya bahut bada ho to None (SQLite overflow se bachne ke liye)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 2 ** 31 else None


def scope_tasks(queryset, params, user):
    """Scope: private projects ke tasks hatao, phir 'My tasks' (mine=1) aur project. Dashboard stats aur board isi par chalte hain."""
    queryset = visible_tasks(queryset, user)
    if params.get('mine') == '1':
        queryset = queryset.filter(assigned_to=user)
    project_id = parse_int(params.get('project'))
    if project_id:
        queryset = queryset.filter(project_id=project_id)
    return queryset


def filter_tasks(queryset, params):
    query = (params.get('q') or '').strip()
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(assigned_to__username__icontains=query)
        )

    status = params.get('status')
    if status in dict(Task.STATUS_CHOICES):
        queryset = queryset.filter(status=status)

    priority = params.get('priority')
    if priority in dict(Task.PRIORITY_CHOICES):
        queryset = queryset.filter(priority=priority)

    assignee_id = parse_int(params.get('assignee'))
    if assignee_id:
        queryset = queryset.filter(assigned_to_id=assignee_id)

    label_id = parse_int(params.get('label'))
    if label_id:
        queryset = queryset.filter(labels__id=label_id)

    if params.get('overdue') == '1':
        queryset = queryset.filter(due_date__lt=timezone.localdate()).exclude(status='Completed')

    return queryset


def sort_tasks(queryset, key):
    # Har sort ke end mein -id: pagination ka order stable rehta hai
    if key == 'oldest':
        return queryset.order_by('created_at', 'id')
    if key == 'due':
        return queryset.order_by(F('due_date').asc(nulls_last=True), '-id')
    if key == 'priority':
        rank = Case(
            When(priority='High', then=0),
            When(priority='Medium', then=1),
            default=2,
            output_field=IntegerField(),
        )
        return queryset.annotate(priority_rank=rank).order_by('priority_rank', '-id')
    if key == 'progress':
        return queryset.order_by('-progress', '-id')
    if key == 'title':
        return queryset.order_by('title', 'id')
    return queryset.order_by('-created_at', '-id')
