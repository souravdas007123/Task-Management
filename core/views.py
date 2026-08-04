from django.shortcuts import render, redirect, get_object_or_404
from .models import Task
from .forms import TaskForm
from django.db.models import Avg, Count

def dashboard(request):
    tasks = Task.objects.all()
    
    # Progress calculations for charts & stats
    total_tasks = tasks.count()
    completed_tasks = tasks.filter(status='Completed').count()
    pending_tasks = tasks.filter(status='Pending').count()
    in_progress_tasks = tasks.filter(status='In Progress').count()
    
    # Overall average project progress
    avg_progress = tasks.aggregate(Avg('progress'))['progress__avg']
    overall_progress = int(avg_progress) if avg_progress else 0

    context = {
        'tasks': tasks,
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'pending_tasks': pending_tasks,
        'in_progress_tasks': in_progress_tasks,
        'overall_progress': overall_progress,
    }
    return render(request, 'core/dashboard.html', context)

def add_task(request):
    if request.method == 'POST':
        form = TaskForm(request.POST, request.FILES)
        if form.is_valid():
            task = form.save(commit=False)
            if request.user.is_authenticated:
                task.created_by = request.user
            task.save()
            return redirect('dashboard')
    else:
        form = TaskForm()
    return render(request, 'core/add_task.html', {'form': form})

def update_task(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if request.method == 'POST':
        form = TaskForm(request.POST, request.FILES, instance=task)
        if form.is_valid():
            form.save()
            return redirect('dashboard')
    else:
        form = TaskForm(instance=task)
    return render(request, 'core/update_task.html', {'form': form})

def task_report(request):
    tasks = Task.objects.all()
    status_filter = request.GET.get('status')
    if status_filter:
        tasks = tasks.filter(status=status_filter)
    
    context = {'tasks': tasks, 'status_filter': status_filter}
    return render(request, 'core/report.html', context)