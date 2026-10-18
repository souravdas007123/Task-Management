"""Chhota JSON API (koi extra package nahi). Login (session) zaroori hai.

GET  /api/tasks/                  list (filters: q, status, priority, assignee, label, project, mine, overdue, sort, page, page_size)
GET  /api/tasks/<id>/             ek task
POST /api/tasks/<id>/status/      status badlo (JSON {"status": "Completed"} ya form field status); board isi ko use karta hai
"""
import json
from functools import wraps

from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from .filters import filter_tasks, scope_tasks, sort_tasks
from .models import Task
from .permissions import can_edit_task
from .services import change_status

MAX_PAGE_SIZE = 100


def api_login_required(view):
    """Anonymous ko login page par redirect nahi, JSON 401 dena."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required.'}, status=401)
        return view(request, *args, **kwargs)
    return wrapper


def task_to_dict(task):
    return {
        'id': task.id,
        'title': task.title,
        'description': task.description,
        'status': task.status,
        'priority': task.priority,
        'progress': task.progress,
        'due_date': task.due_date.isoformat() if task.due_date else None,
        'is_overdue': task.is_overdue,
        'assigned_to': task.assigned_to.username,
        'created_by': task.created_by.username if task.created_by_id else None,
        'project': task.project.name if task.project_id else None,
        'labels': [label.name for label in task.labels.all()],
        'recurrence': task.recurrence,
        'created_at': task.created_at.isoformat(),
        'completed_at': task.completed_at.isoformat() if task.completed_at else None,
        'url': task.get_absolute_url(),
    }


def _bounded_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))


@api_login_required
@require_GET
def api_task_list(request):
    params = request.GET
    queryset = scope_tasks(
        Task.objects.select_related('assigned_to', 'created_by', 'project').prefetch_related('labels'),
        params, request.user,
    )
    queryset = sort_tasks(filter_tasks(queryset, params), params.get('sort'))

    page_size = _bounded_int(params.get('page_size'), 25, 1, MAX_PAGE_SIZE)
    page = Paginator(queryset, page_size).get_page(params.get('page'))
    return JsonResponse({
        'count': page.paginator.count,
        'page': page.number,
        'pages': page.paginator.num_pages,
        'results': [task_to_dict(task) for task in page.object_list],
    })


@api_login_required
@require_GET
def api_task_detail(request, pk):
    task = get_object_or_404(
        Task.objects.select_related('assigned_to', 'created_by', 'project').prefetch_related('labels'), pk=pk,
    )
    return JsonResponse(task_to_dict(task))


@api_login_required
@require_POST
def api_task_status(request, pk):
    task = get_object_or_404(Task.objects.select_related('assigned_to', 'created_by', 'project'), pk=pk)
    if not can_edit_task(request.user, task):
        return JsonResponse({'error': 'You cannot change this task.'}, status=403)

    new_status = None
    if request.content_type == 'application/json':
        try:
            payload = json.loads(request.body or b'{}')
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)
        if isinstance(payload, dict):
            new_status = payload.get('status')
    else:
        new_status = request.POST.get('status')

    if new_status not in dict(Task.STATUS_CHOICES):
        return JsonResponse({'error': 'Invalid status.', 'allowed': [s for s, _ in Task.STATUS_CHOICES]}, status=400)

    change_status(task, new_status, request.user, request=request)
    task.refresh_from_db()
    return JsonResponse(task_to_dict(task))
