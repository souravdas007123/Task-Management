"""Budget aur expenses.

- Budget overview aur project budget: sirf admin/manager.
- Expense submit: koi bhi login user. Manager ka expense turant approved, baaki ka "waiting for review".
- Approve/reject: sirf admin/manager. Budget mein sirf approved expenses ginte hain.
"""
import csv

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .budget import (
    budget_managers, hourly_rates, money, project_budget_summary, refresh_budget_alert,
)
from .filters import parse_int
from .forms import ExpenseForm
from .models import Expense, Project, Task
from .permissions import is_manager, manager_required, visible_projects, visible_tasks
from .services import display_name, log_activity, notify, notify_many
from .views_reports import _csv_safe

PAGE_SIZE = 20


def _amount(value):
    return f'{settings.CURRENCY_SYMBOL}{money(value):,}'


def _back(request, default):
    target = request.POST.get('next') or ''
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return default


@login_required
@manager_required
def budget_overview(request):
    rates = hourly_rates()
    rows = []
    for project in Project.objects.all():
        summary = project_budget_summary(project, rates)
        rows.append({'project': project, **summary})

    # Jin projects ka budget hai unme se jyada use hua wala upar
    rows.sort(key=lambda r: (r['percent'] is None, -(r['percent'] or 0), r['project'].name.lower()))

    with_budget = [r for r in rows if r['has_budget']]
    total_budget = sum((r['budget'] for r in with_budget), start=money(0))
    total_spent_budgeted = sum((r['spent'] for r in with_budget), start=money(0))
    total_spent_all = sum((r['spent'] for r in rows), start=money(0))

    return render(request, 'core/budget_overview.html', {
        'rows': rows,
        'total_budget': total_budget,
        'total_spent_budgeted': total_spent_budgeted,
        'total_spent_all': total_spent_all,
        'total_remaining': total_budget - total_spent_budgeted,
        'pending_review': Expense.objects.filter(status='submitted').count(),
        'over_count': sum(1 for r in rows if r['state'] == 'over'),
    })


@login_required
@manager_required
def project_budget(request, pk):
    project = get_object_or_404(Project, pk=pk)
    summary = project_budget_summary(project)
    expenses = project.expenses.select_related('submitted_by', 'task')[:100]
    return render(request, 'core/project_budget.html', {
        'project': project,
        's': summary,
        'expenses': expenses,
    })


@login_required
def expense_list(request):
    manager = is_manager(request.user)
    scope = Expense.objects.select_related('project', 'task', 'submitted_by')
    if not manager:
        scope = scope.filter(submitted_by=request.user)

    project_id = parse_int(request.GET.get('project'))
    if project_id:
        scope = scope.filter(project_id=project_id)
    category = request.GET.get('category')
    if category in dict(Expense.CATEGORY_CHOICES):
        scope = scope.filter(category=category)
    else:
        category = ''

    totals = {
        row['status']: row['total']
        for row in scope.order_by().values('status').annotate(total=Sum('amount'))
    }

    status = request.GET.get('status')
    filtered = scope.filter(status=status) if status in dict(Expense.STATUS_CHOICES) else scope
    if status not in dict(Expense.STATUS_CHOICES):
        status = ''

    page_obj = Paginator(filtered, PAGE_SIZE).get_page(request.GET.get('page'))
    for expense in page_obj.object_list:
        expense.can_delete = manager or (expense.submitted_by_id == request.user.id and expense.status == 'submitted')

    query = request.GET.copy()
    query.pop('page', None)

    return render(request, 'core/expenses.html', {
        'expenses': page_obj,
        'page_obj': page_obj,
        'querystring': query.urlencode(),
        'manager': manager,
        'totals_approved': totals.get('approved', 0),
        'totals_pending': totals.get('submitted', 0),
        'totals_rejected': totals.get('rejected', 0),
        'projects': visible_projects(Project.objects.all(), request.user),
        'status_choices': Expense.STATUS_CHOICES,
        'category_choices': Expense.CATEGORY_CHOICES,
        'current': {'status': status, 'category': category, 'project': project_id},
        'next_url': request.get_full_path(),
    })


def _limit_choices(form, user):
    """Private projects aur unke tasks expense form ke dropdown mein sirf unhe dikhein jinhe access hai."""
    form.fields['project'].queryset = visible_projects(Project.objects.all(), user)
    form.fields['task'].queryset = visible_tasks(Task.objects.order_by('-created_at'), user)


@login_required
def expense_add(request):
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES)
        _limit_choices(form, request.user)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.submitted_by = request.user
            manager = is_manager(request.user)
            if manager:
                expense.status = 'approved'
                expense.reviewed_by = request.user
                expense.reviewed_at = timezone.now()
            expense.save()

            amount_text = _amount(expense.amount)
            if expense.task_id:
                log_activity(expense.task, request.user, f'Added expense "{expense.title}" ({amount_text})')

            if manager:
                refresh_budget_alert(expense.project, request)
                messages.success(request, 'Expense added and approved.')
            else:
                notify_many(
                    budget_managers(),
                    f'{display_name(request.user)} submitted an expense of {amount_text} for "{expense.project.name}"',
                    link=reverse('expense_list') + '?status=submitted', actor=request.user, request=request,
                )
                messages.success(request, 'Expense submitted. A manager will review it.')
            return redirect('expense_list')
    else:
        initial = {}
        task_id = parse_int(request.GET.get('task'))
        task = visible_tasks(Task.objects.all(), request.user).filter(pk=task_id).first() if task_id else None
        project_id = parse_int(request.GET.get('project'))
        if task:
            initial['task'] = task.pk
            if task.project_id:
                initial['project'] = task.project_id
        if project_id and 'project' not in initial:
            initial['project'] = project_id
        form = ExpenseForm(initial=initial)
        _limit_choices(form, request.user)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Add Expense', 'back_url': 'expense_list',
        'help': 'Managers\' expenses are approved straight away. Everyone else\'s are reviewed first.',
    })


def _review(request, pk, new_status):
    expense = get_object_or_404(Expense.objects.select_related('project', 'task', 'submitted_by'), pk=pk)
    if expense.status != new_status:
        note = (request.POST.get('note') or '').strip()[:300] if new_status == 'rejected' else ''
        expense.status = new_status
        expense.reviewed_by = request.user
        expense.reviewed_at = timezone.now()
        expense.review_note = note
        expense.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])

        word = 'approved' if new_status == 'approved' else 'rejected'
        message = f'Your expense "{expense.title}" was {word}'
        if note:
            message += f': {note}'
        notify(expense.submitted_by, message, link=reverse('expense_list'), actor=request.user, request=request)
        if expense.task_id:
            log_activity(expense.task, request.user, f'Expense "{expense.title}" ({_amount(expense.amount)}) {word}')
        refresh_budget_alert(expense.project, request)
        messages.success(request, f'Expense {word}.')
    return redirect(_back(request, reverse('expense_list')))


@login_required
@manager_required
@require_POST
def expense_approve(request, pk):
    return _review(request, pk, 'approved')


@login_required
@manager_required
@require_POST
def expense_reject(request, pk):
    return _review(request, pk, 'rejected')


@login_required
@require_POST
def expense_delete(request, pk):
    expense = get_object_or_404(Expense.objects.select_related('project'), pk=pk)
    allowed = is_manager(request.user) or (expense.submitted_by_id == request.user.id and expense.status == 'submitted')
    if not allowed:
        raise PermissionDenied
    project = expense.project
    if expense.receipt:
        expense.receipt.delete(save=False)
    expense.delete()
    refresh_budget_alert(project, request)
    messages.success(request, 'Expense deleted.')
    return redirect(_back(request, reverse('expense_list')))


@login_required
@manager_required
def expense_export(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="expenses.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['Date', 'Project', 'Task', 'Title', 'Category', 'Amount', 'Status', 'Submitted by', 'Reviewed by', 'Note'])
    queryset = Expense.objects.select_related('project', 'task', 'submitted_by', 'reviewed_by')
    for expense in queryset:
        writer.writerow([
            expense.incurred_on.isoformat(),
            _csv_safe(expense.project.name),
            _csv_safe(expense.task.title if expense.task_id else ''),
            _csv_safe(expense.title),
            expense.get_category_display(),
            expense.amount,
            expense.get_status_display(),
            _csv_safe(expense.submitted_by.username if expense.submitted_by_id else ''),
            _csv_safe(expense.reviewed_by.username if expense.reviewed_by_id else ''),
            _csv_safe(expense.note),
        ])
    return response
