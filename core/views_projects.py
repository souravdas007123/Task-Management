"""Projects aur labels (sirf admin/manager manage karte hain, baaki dekh sakte hain)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import LabelForm, ProjectForm
from .models import Label, Project
from .permissions import manager_required


@login_required
def project_list(request):
    projects = Project.objects.annotate(
        total=Count('tasks'),
        done=Count('tasks', filter=Q(tasks__status='Completed')),
        avg_progress=Avg('tasks__progress'),
    )
    for project in projects:
        project.progress = int(project.avg_progress or 0)
    return render(request, 'core/projects.html', {'projects': projects})


@login_required
@manager_required
def project_add(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.created_by = request.user
            project.save()
            messages.success(request, 'Project created.')
            return redirect('project_list')
    else:
        form = ProjectForm()
    return render(request, 'core/simple_form.html', {'form': form, 'page_title': 'Add Project', 'back_url': 'project_list'})


@login_required
@manager_required
def project_edit(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            messages.success(request, 'Project updated.')
            return redirect('project_list')
    else:
        form = ProjectForm(instance=project)
    return render(request, 'core/simple_form.html', {'form': form, 'page_title': 'Edit Project', 'back_url': 'project_list'})


@login_required
@manager_required
def project_delete(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        name = project.name
        project.delete()  # tasks delete nahi hote, bas project khali ho jata hai (SET_NULL)
        messages.success(request, f'Project "{name}" deleted. Its tasks were kept.')
        return redirect('project_list')
    return render(request, 'core/confirm_delete.html', {
        'page_title': 'Delete project',
        'heading': f'Delete "{project.name}"?',
        'message': 'The tasks in this project are kept, but they will no longer belong to any project.',
        'cancel_url': reverse('project_list'),
    })


@login_required
def label_list(request):
    form = LabelForm()
    labels = Label.objects.annotate(task_count=Count('tasks'))
    return render(request, 'core/labels.html', {'labels': labels, 'form': form})


@login_required
@manager_required
@require_POST
def label_add(request):
    form = LabelForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, 'Label added.')
    else:
        errors = ' '.join(e for field_errors in form.errors.values() for e in field_errors)
        messages.error(request, errors or 'Could not add the label.')
    return redirect('label_list')


@login_required
@manager_required
@require_POST
def label_delete(request, pk):
    label = get_object_or_404(Label, pk=pk)
    name = label.name
    label.delete()
    messages.success(request, f'Label "{name}" deleted.')
    return redirect('label_list')
