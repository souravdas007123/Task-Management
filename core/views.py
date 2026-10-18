"""Task ke views: dashboard, add/update/delete, detail aur uske actions (comments, checklist, files, time)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .budget import refresh_budget_alert
from .filters import SORT_CHOICES, filter_tasks, parse_int, scope_tasks, sort_tasks
from .forms import AttachmentForm, ChecklistItemForm, CommentForm, TaskForm, TimeEntryForm
from .models import Attachment, ChecklistItem, Comment, Label, Project, Task, TimeEntry
from .permissions import can_delete_task, can_edit_task, is_manager
from .services import (
    annotate_permissions, diff_snapshots, display_name, log_activity, notify,
    notify_many, record_task_changes, snapshot_task,
)

PAGE_SIZE = 10
FILTER_KEYS = ('q', 'status', 'priority', 'assignee', 'label', 'overdue')


def task_queryset():
    return Task.objects.select_related('assigned_to', 'project').prefetch_related('labels')


def form_error_text(form):
    """Form ke saare errors ek line mein (messages ke liye)."""
    errors = []
    for field_errors in form.errors.values():
        errors.extend(field_errors)
    return ' '.join(errors) or 'Please check the form and try again.'


@login_required
def dashboard(request):
    params = request.GET
    # Plain queryset: counts/aggregates ke liye. select_related/prefetch sirf list ke liye neeche lagta hai.
    scope = scope_tasks(Task.objects.all(), params, request.user)

    # Ek hi query mein status-wise counts
    counts = dict(scope.order_by().values_list('status').annotate(total=Count('id')))
    total_tasks = sum(counts.values())
    completed_tasks = counts.get('Completed', 0)
    pending_tasks = counts.get('Pending', 0)
    in_progress_tasks = counts.get('In Progress', 0)
    on_hold_tasks = counts.get('On Hold', 0)

    avg_progress = scope.aggregate(avg=Avg('progress'))['avg']
    overall_progress = int(avg_progress) if avg_progress else 0
    overdue_count = scope.filter(due_date__lt=timezone.localdate()).exclude(status='Completed').count()

    # Dashboard ke segmented bar ke liye: har status ka count aur width (%)
    status_segments = [
        {'label': label, 'css': css, 'count': count,
         'pct': round(count * 100 / total_tasks) if total_tasks else 0,
         'width': round(count * 100 / total_tasks, 2) if total_tasks else 0}
        for label, css, count in [
            ('Completed', 'completed', completed_tasks),
            ('In Progress', 'progress', in_progress_tasks),
            ('Pending', 'pending', pending_tasks),
            ('On Hold', 'hold', on_hold_tasks),
        ]
    ]

    filtered = sort_tasks(filter_tasks(scope, params), params.get('sort')).select_related(
        'assigned_to', 'project',
    ).prefetch_related('labels')
    page_obj = Paginator(filtered, PAGE_SIZE).get_page(params.get('page'))
    annotate_permissions(request.user, page_obj.object_list)

    query = params.copy()
    query.pop('page', None)

    context = {
        'tasks': page_obj,
        'page_obj': page_obj,
        'querystring': query.urlencode(),
        'filters_active': any(params.get(key) for key in FILTER_KEYS),
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'pending_tasks': pending_tasks,
        'in_progress_tasks': in_progress_tasks,
        'on_hold_tasks': on_hold_tasks,
        'overall_progress': overall_progress,
        'overdue_count': overdue_count,
        'status_segments': status_segments,
        # filter bar
        'mine': params.get('mine') == '1',
        'current': {key: params.get(key, '') for key in FILTER_KEYS + ('sort', 'project')},
        'status_choices': Task.STATUS_CHOICES,
        'priority_choices': Task.PRIORITY_CHOICES,
        'sort_choices': SORT_CHOICES,
        'users': User.objects.filter(is_active=True).order_by('username'),
        'projects': Project.objects.all(),
        'labels': Label.objects.all(),
        'current_project': Project.objects.filter(pk=parse_int(params.get('project'))).first(),
    }
    return render(request, 'core/dashboard.html', context)


@login_required
def add_task(request):
    if request.method == 'POST':
        form = TaskForm(request.POST, request.FILES)
        if form.is_valid():
            task = form.save(commit=False)
            task.created_by = request.user
            task.save()
            form.save_m2m()
            log_activity(task, request.user, 'Created the task')
            notify(
                task.assigned_to,
                f'{display_name(request.user)} assigned you "{task.title}"',
                link=task.get_absolute_url(), actor=request.user, request=request,
            )
            messages.success(request, 'Task added.')
            return redirect('dashboard')
    else:
        initial = {}
        project_id = parse_int(request.GET.get('project'))
        if project_id:
            initial['project'] = project_id
        form = TaskForm(initial=initial)
    return render(request, 'core/add_task.html', {'form': form, 'page_title': 'Add Task'})


@login_required
def update_task(request, pk):
    task = get_object_or_404(task_queryset(), pk=pk)
    if not can_edit_task(request.user, task):
        raise PermissionDenied

    if request.method == 'POST':
        before = snapshot_task(task)
        form = TaskForm(request.POST, request.FILES, instance=task)
        if form.is_valid():
            task = form.save()
            changes = diff_snapshots(before, snapshot_task(task))
            record_task_changes(task, request.user, changes, request=request)
            messages.success(request, 'Task updated.')
            return redirect('dashboard')
    else:
        form = TaskForm(instance=task)
    return render(request, 'core/update_task.html', {'form': form, 'page_title': 'Update Task', 'task': task})


@login_required
def delete_task(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not can_delete_task(request.user, task):
        raise PermissionDenied

    if request.method == 'POST':
        # Disk se files bhi hatao, sirf DB row nahi
        for attachment in task.attachments.all():
            attachment.file.delete(save=False)
        if task.issue_image:
            task.issue_image.delete(save=False)
        title = task.title
        task.delete()
        messages.success(request, f'Task "{title}" deleted.')
        return redirect('dashboard')

    return render(request, 'core/confirm_delete.html', {
        'page_title': 'Delete task',
        'heading': f'Delete "{task.title}"?',
        'message': 'This also deletes its comments, checklist, files and history. This cannot be undone.',
        'cancel_url': task.get_absolute_url(),
    })


def format_minutes(total):
    hours, minutes = divmod(total, 60)
    if hours and minutes:
        return f'{hours}h {minutes}m'
    return f'{hours}h' if hours else f'{minutes}m'


@login_required
def task_detail(request, pk):
    task = get_object_or_404(
        Task.objects.select_related('assigned_to', 'created_by', 'project').prefetch_related('labels'),
        pk=pk,
    )
    checklist = list(task.checklist.all())
    time_entries = list(task.time_entries.select_related('user'))
    can_edit = can_edit_task(request.user, task)
    manager = is_manager(request.user)
    for entry in time_entries:
        entry.can_delete = manager or entry.user_id == request.user.id

    # Estimate vs actual
    logged_minutes = sum(entry.minutes for entry in time_entries)
    effort = None
    if task.estimated_hours:
        estimated_minutes = task.estimated_hours * 60
        percent = int(logged_minutes * 100 / estimated_minutes)
        effort = {'percent': percent, 'bar': min(percent, 100), 'over': logged_minutes > estimated_minutes}

    context = {
        'effort': effort,
        'task': task,
        'can_edit': can_edit,
        'can_delete': can_delete_task(request.user, task),
        'checklist': checklist,
        'checklist_done': sum(1 for item in checklist if item.is_done),
        'comments': task.comments.select_related('author'),
        'attachments': task.attachments.select_related('uploaded_by'),
        'time_entries': time_entries,
        'total_time': format_minutes(sum(entry.minutes for entry in time_entries)),
        'activity': task.activity.select_related('actor')[:30],
        'comment_form': CommentForm(),
        'checklist_form': ChecklistItemForm(),
        'attachment_form': AttachmentForm(),
        'time_form': TimeEntryForm(),
        'page_title': task.title,
    }
    return render(request, 'core/task_detail.html', context)


def _editable_task_or_403(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not can_edit_task(request.user, task):
        raise PermissionDenied
    return task


@login_required
@require_POST
def add_comment(request, pk):
    task = get_object_or_404(Task, pk=pk)
    form = CommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.task = task
        comment.author = request.user
        comment.save()
        log_activity(task, request.user, 'Added a comment')
        notify_many(
            [task.assigned_to, task.created_by],
            f'{display_name(request.user)} commented on "{task.title}"',
            link=task.get_absolute_url() + '#comments', actor=request.user, request=request,
        )
        messages.success(request, 'Comment added.')
    else:
        messages.error(request, form_error_text(form))
    return redirect(task.get_absolute_url() + '#comments')


@login_required
@require_POST
def delete_comment(request, pk):
    comment = get_object_or_404(Comment, pk=pk)
    task = comment.task
    if not (is_manager(request.user) or comment.author_id == request.user.id):
        raise PermissionDenied
    comment.delete()
    messages.success(request, 'Comment deleted.')
    return redirect(task.get_absolute_url() + '#comments')


@login_required
@require_POST
def add_checklist_item(request, pk):
    task = _editable_task_or_403(request, pk)
    form = ChecklistItemForm(request.POST)
    if form.is_valid():
        item = form.save(commit=False)
        item.task = task
        item.position = (task.checklist.aggregate(last=Max('position'))['last'] or 0) + 1
        item.save()
        task.sync_progress_from_checklist()
        log_activity(task, request.user, f'Added checklist item "{item.text}"')
    else:
        messages.error(request, form_error_text(form))
    return redirect(task.get_absolute_url() + '#checklist')


@login_required
@require_POST
def toggle_checklist_item(request, pk):
    item = get_object_or_404(ChecklistItem.objects.select_related('task'), pk=pk)
    task = item.task
    if not can_edit_task(request.user, task):
        raise PermissionDenied
    item.is_done = not item.is_done
    item.save(update_fields=['is_done'])
    task.sync_progress_from_checklist()
    log_activity(task, request.user, f'{"Checked" if item.is_done else "Unchecked"} "{item.text}"')
    return redirect(task.get_absolute_url() + '#checklist')


@login_required
@require_POST
def delete_checklist_item(request, pk):
    item = get_object_or_404(ChecklistItem.objects.select_related('task'), pk=pk)
    task = item.task
    if not can_edit_task(request.user, task):
        raise PermissionDenied
    text = item.text
    item.delete()
    task.sync_progress_from_checklist()
    log_activity(task, request.user, f'Removed checklist item "{text}"')
    return redirect(task.get_absolute_url() + '#checklist')


@login_required
@require_POST
def add_attachment(request, pk):
    task = _editable_task_or_403(request, pk)
    form = AttachmentForm(request.POST, request.FILES)
    if form.is_valid():
        uploaded = form.cleaned_data['file']
        Attachment.objects.create(
            task=task, file=uploaded, original_name=uploaded.name[:255], uploaded_by=request.user,
        )
        log_activity(task, request.user, f'Attached "{uploaded.name}"'[:300])
        messages.success(request, 'File attached.')
    else:
        messages.error(request, form_error_text(form))
    return redirect(task.get_absolute_url() + '#files')


@login_required
@require_POST
def delete_attachment(request, pk):
    attachment = get_object_or_404(Attachment.objects.select_related('task'), pk=pk)
    task = attachment.task
    if not can_edit_task(request.user, task):
        raise PermissionDenied
    name = attachment.original_name or attachment.file.name
    attachment.file.delete(save=False)
    attachment.delete()
    log_activity(task, request.user, f'Removed file "{name}"'[:300])
    messages.success(request, 'File removed.')
    return redirect(task.get_absolute_url() + '#files')


@login_required
@require_POST
def add_time_entry(request, pk):
    task = _editable_task_or_403(request, pk)
    form = TimeEntryForm(request.POST)
    if form.is_valid():
        entry = form.save(task, request.user)
        log_activity(task, request.user, f'Logged {entry.duration_display} of work')
        if task.project_id:  # labour cost badhi, budget alert check karo
            refresh_budget_alert(task.project, request)
        messages.success(request, 'Time logged.')
    else:
        messages.error(request, form_error_text(form))
    return redirect(task.get_absolute_url() + '#time')


@login_required
@require_POST
def delete_time_entry(request, pk):
    entry = get_object_or_404(TimeEntry.objects.select_related('task'), pk=pk)
    task = entry.task
    if not (is_manager(request.user) or entry.user_id == request.user.id):
        raise PermissionDenied
    entry.delete()
    if task.project_id:
        refresh_budget_alert(task.project, request)
    messages.success(request, 'Time entry deleted.')
    return redirect(task.get_absolute_url() + '#time')
