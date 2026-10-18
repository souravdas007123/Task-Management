from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg
from django.shortcuts import render, redirect, get_object_or_404
from .models import Task
from .forms import TaskForm


@login_required
def dashboard(request):
    # select_related: har row par assigned_to ke liye alag query na chale (N+1 fix)
    tasks = Task.objects.select_related('assigned_to')

    # Progress calculations for charts & stats
    total_tasks = tasks.count()
    completed_tasks = tasks.filter(status='Completed').count()
    pending_tasks = tasks.filter(status='Pending').count()
    in_progress_tasks = tasks.filter(status='In Progress').count()
    on_hold_tasks = tasks.filter(status='On Hold').count()

    # Overall average project progress
    avg_progress = tasks.aggregate(avg=Avg('progress'))['avg']
    overall_progress = int(avg_progress) if avg_progress else 0

    context = {
        'tasks': tasks,
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'pending_tasks': pending_tasks,
        'in_progress_tasks': in_progress_tasks,
        'on_hold_tasks': on_hold_tasks,
        'overall_progress': overall_progress,
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
            messages.success(request, 'Task add ho gaya.')
            return redirect('dashboard')
    else:
        form = TaskForm()
    return render(request, 'core/add_task.html', {'form': form, 'page_title': 'Add Task'})


@login_required
def update_task(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if request.method == 'POST':
        form = TaskForm(request.POST, request.FILES, instance=task)
        if form.is_valid():
            form.save()
            messages.success(request, 'Task update ho gaya.')
            return redirect('dashboard')
    else:
        form = TaskForm(instance=task)
    return render(request, 'core/update_task.html', {'form': form, 'page_title': 'Update Task'})


@login_required
def task_report(request):
    tasks = Task.objects.select_related('assigned_to')
    status_filter = request.GET.get('status')
    # Sirf valid status hi filter hone do; galat value par saare tasks dikhao
    if status_filter in dict(Task.STATUS_CHOICES):
        tasks = tasks.filter(status=status_filter)
    else:
        status_filter = None

    context = {
        'tasks': tasks,
        'status_filter': status_filter,
        'status_choices': Task.STATUS_CHOICES,
    }
    return render(request, 'core/report.html', context)
