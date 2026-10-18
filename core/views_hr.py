"""HR: employees ki directory, profile, leave policy, holidays, salary aur payslips.

Salary/payslip sirf admin/HR role (aur apni khud ki published payslip employee) dekh sakta hai. Manager bhi nahi.
"""
import csv
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    DepartmentForm, EmployeeForm, HolidayForm, MyContactForm, PayslipAdjustForm, SalaryStructureForm,
)
from .hr import DEFAULT_POLICY, leave_balance, policies
from .models import AssetAssignment, Department, EmployeeChecklist, Holiday, LeavePolicy, LeaveRequest, Payslip, SalaryStructure, UserProfile
from .payroll import generate_month, recalculate
from .permissions import hr_required, is_hr
from .services import display_name, notify
from .views_reports import _csv_safe

MONTH_NAMES = ['', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
               'September', 'October', 'November', 'December']


def month_label(year, month):
    return f'{MONTH_NAMES[month]} {year}'


def _parse_month(value):
    """'2026-10' -> (2026, 10). Galat ho to is mahine ka."""
    today = timezone.localdate()
    try:
        year_text, month_text = (value or '').split('-')
        year, month = int(year_text), int(month_text)
    except ValueError:
        return today.year, today.month
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        return today.year, today.month
    return year, month


def _month_key(year, month):
    return f'{year}-{month:02d}'


def _shift_month(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _profile_for(user):
    profile, _created = UserProfile.objects.get_or_create(user=user)
    return profile


def _name(user):
    return user.get_full_name() or user.username


# ---------------------------------------------------------------- sabke liye

@login_required
def people(request):
    users = User.objects.filter(is_active=True).select_related('profile__department', 'profile__reporting_manager')
    query = (request.GET.get('q') or '').strip()
    if query:
        users = users.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query)
            | Q(profile__designation__icontains=query) | Q(profile__department__name__icontains=query)
        )
    department_id = request.GET.get('department')
    department_id = int(department_id) if (department_id or '').isdigit() and int(department_id) < 2 ** 31 else None
    if department_id:
        users = users.filter(profile__department_id=department_id)
    return render(request, 'core/people.html', {
        'people': users.order_by('username'),
        'departments': Department.objects.all(),
        'current': {'q': query, 'department': department_id},
        'hr': is_hr(request.user),
    })


@login_required
def org_chart(request):
    users = list(User.objects.filter(is_active=True).select_related('profile').order_by('username'))
    by_id = {u.pk: u for u in users}
    children = {}
    roots = []
    for user in users:
        profile = getattr(user, 'profile', None)
        boss_id = profile.reporting_manager_id if profile else None
        if boss_id in by_id and boss_id != user.pk:
            children.setdefault(boss_id, []).append(user)
        else:
            roots.append(user)

    rows, seen = [], set()

    def walk(user, depth):
        if user.pk in seen or depth > 30:
            return
        seen.add(user.pk)
        rows.append({'user': user, 'depth': depth, 'indent': depth * 28,
                     'reports': len(children.get(user.pk, []))})
        for child in children.get(user.pk, []):
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)
    for user in users:  # loop mein phase log bhi dikhao
        if user.pk not in seen:
            walk(user, 0)
    return render(request, 'core/org_chart.html', {'rows': rows})


@login_required
def my_profile(request):
    profile = _profile_for(request.user)
    if request.method == 'POST':
        form = MyContactForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your contact details were saved.')
            return redirect('my_profile')
    else:
        form = MyContactForm(instance=profile)
    year = timezone.localdate().year
    return render(request, 'core/my_profile.html', {
        'profile': profile,
        'form': form,
        'balance': leave_balance(request.user, year),
        'year': year,
        'payslip_count': Payslip.objects.filter(user=request.user, status='published').count(),
        'my_assets': AssetAssignment.objects.filter(user=request.user, returned_on__isnull=True).select_related('asset'),
        'my_checklists': EmployeeChecklist.objects.filter(user=request.user, completed_at__isnull=True),
    })


@login_required
def holiday_list(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get('year', today.year))
    except ValueError:
        year = today.year
    if not (2000 <= year <= 2100):
        year = today.year
    return render(request, 'core/holidays.html', {
        'holidays': Holiday.objects.filter(date__year=year),
        'year': year,
        'form': HolidayForm(),
        'hr': is_hr(request.user),
        'today': today,
    })


@login_required
def my_payslips(request):
    slips = Payslip.objects.filter(user=request.user, status='published')
    return render(request, 'core/payslips.html', {'payslips': slips, 'month_label': month_label})


@login_required
def payslip_detail(request, pk):
    payslip = get_object_or_404(Payslip.objects.select_related('user', 'user__profile__department'), pk=pk)
    # Dusre ki slip (ya draft) ko 404: wo exist hi na kare
    if not (is_hr(request.user) or (payslip.user_id == request.user.id and payslip.status == 'published')):
        raise Http404
    profile = getattr(payslip.user, 'profile', None)
    return render(request, 'core/payslip_detail.html', {
        'payslip': payslip, 'profile': profile, 'period': month_label(payslip.year, payslip.month),
        'hr': is_hr(request.user),
    })


# ---------------------------------------------------------------- sirf HR

def _latest_gross():
    """{user_id: SalaryStructure} aaj tak ka latest, ek hi query mein."""
    latest = {}
    rows = SalaryStructure.objects.filter(effective_from__lte=timezone.localdate()).order_by('user_id', '-effective_from', '-id')
    for row in rows:
        latest.setdefault(row.user_id, row)
    return latest


@login_required
@hr_required
def hr_employees(request):
    users = list(User.objects.filter(is_active=True).select_related('profile__department').order_by('username'))
    structures = _latest_gross()
    for user in users:
        user.structure = structures.get(user.pk)
    return render(request, 'core/hr_employees.html', {'employees': users})


@login_required
@hr_required
def employee_edit(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    profile = _profile_for(user)
    if request.method == 'POST':
        form = EmployeeForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, f'Saved the details of {_name(user)}.')
            return redirect('hr_employees')
    else:
        form = EmployeeForm(instance=profile)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': f'Employee: {_name(user)}', 'back_url': 'hr_employees',
        'help': 'Post, department, joining date, manager and contact details. Salary is on its own page.',
    })


@login_required
@hr_required
def salary_history(request, user_id):
    employee = get_object_or_404(User, pk=user_id)
    if request.method == 'POST':
        form = SalaryStructureForm(request.POST)
        if form.is_valid():
            structure = form.save(commit=False)
            structure.user = employee
            structure.created_by = request.user
            structure.save()
            messages.success(request, 'New salary structure saved.')
            return redirect('salary_history', user_id=employee.pk)
    else:
        form = SalaryStructureForm(initial={'effective_from': timezone.localdate().replace(day=1), 'pf_percent': 12})
    return render(request, 'core/salary_history.html', {
        'employee': employee,
        'structures': employee.salary_structures.select_related('created_by'),
        'form': form,
    })


@login_required
@hr_required
@require_POST
def salary_delete(request, pk):
    structure = get_object_or_404(SalaryStructure, pk=pk)
    user_id = structure.user_id
    structure.delete()  # purani payslips snapshot hain, unpar asar nahi
    messages.success(request, 'Salary structure deleted. Existing payslips are not changed.')
    return redirect('salary_history', user_id=user_id)


@login_required
@hr_required
def payroll(request):
    year, month = _parse_month(request.GET.get('month'))
    slips = list(Payslip.objects.filter(year=year, month=month).select_related('user'))
    totals = {
        'gross': sum((s.gross for s in slips), Decimal('0')),
        'deductions': sum((s.total_deductions for s in slips), Decimal('0')),
        'bonus': sum((s.bonus for s in slips), Decimal('0')),
        'net': sum((s.net for s in slips), Decimal('0')),
    }
    with_slip = {s.user_id for s in slips}
    without_structure = [
        u for u in User.objects.filter(is_active=True).order_by('username')
        if u.pk not in with_slip and not u.salary_structures.exists()
    ]
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)
    return render(request, 'core/payroll.html', {
        'slips': slips,
        'totals': totals,
        'month_key': _month_key(year, month),
        'label': month_label(year, month),
        'prev_key': _month_key(prev_year, prev_month),
        'next_key': _month_key(next_year, next_month),
        'drafts': sum(1 for s in slips if s.status == 'draft'),
        'without_structure': without_structure,
    })


def _month_from_post(request):
    return _parse_month(request.POST.get('month'))


def _payroll_redirect(year, month):
    return redirect(reverse('payroll') + f'?month={_month_key(year, month)}')


@login_required
@hr_required
@require_POST
def payroll_generate(request):
    year, month = _month_from_post(request)
    created, skipped = generate_month(User.objects.filter(is_active=True), year, month, request.user)
    if created:
        messages.success(request, f'Created {created} draft payslip{"s" if created != 1 else ""} for {month_label(year, month)}.')
    else:
        messages.info(request, 'No new payslips were needed. Everyone eligible already has one, or has no salary set.')
    return _payroll_redirect(year, month)


def _publish(request, payslip):
    payslip.status = 'published'
    payslip.published_at = timezone.now()
    payslip.save(update_fields=['status', 'published_at'])
    notify(
        payslip.user, f'Your payslip for {month_label(payslip.year, payslip.month)} is ready',
        link=reverse('payslip_detail', args=[payslip.pk]), actor=request.user, request=request,
    )


@login_required
@hr_required
@require_POST
def payroll_publish_all(request):
    year, month = _month_from_post(request)
    drafts = list(Payslip.objects.filter(year=year, month=month, status='draft').select_related('user'))
    for payslip in drafts:
        _publish(request, payslip)
    messages.success(request, f'Published {len(drafts)} payslip{"s" if len(drafts) != 1 else ""}.')
    return _payroll_redirect(year, month)


def _slip(pk):
    return get_object_or_404(Payslip.objects.select_related('user'), pk=pk)


@login_required
@hr_required
@require_POST
def payslip_publish(request, pk):
    payslip = _slip(pk)
    if payslip.status == 'draft':
        _publish(request, payslip)
        messages.success(request, 'Payslip published.')
    return _payroll_redirect(payslip.year, payslip.month)


@login_required
@hr_required
@require_POST
def payslip_reopen(request, pk):
    payslip = _slip(pk)
    if payslip.status == 'published':
        payslip.status = 'draft'
        payslip.published_at = None
        payslip.save(update_fields=['status', 'published_at'])
        messages.success(request, 'Payslip moved back to draft. The employee can no longer see it.')
    return _payroll_redirect(payslip.year, payslip.month)


@login_required
@hr_required
@require_POST
def payslip_recalculate(request, pk):
    payslip = _slip(pk)
    if payslip.status != 'draft':
        messages.error(request, 'Only a draft payslip can be recalculated.')
    elif recalculate(payslip):
        messages.success(request, 'Payslip recalculated from the current salary and leave.')
    else:
        messages.error(request, 'No salary structure applies to this month any more.')
    return _payroll_redirect(payslip.year, payslip.month)


@login_required
@hr_required
@require_POST
def payslip_delete(request, pk):
    payslip = _slip(pk)
    if payslip.status != 'draft':
        messages.error(request, 'Move the payslip back to draft before deleting it.')
    else:
        payslip.delete()
        messages.success(request, 'Draft payslip deleted.')
    return _payroll_redirect(payslip.year, payslip.month)


@login_required
@hr_required
def payslip_adjust(request, pk):
    payslip = _slip(pk)
    if payslip.status != 'draft':
        messages.error(request, 'Only a draft payslip can be changed.')
        return _payroll_redirect(payslip.year, payslip.month)
    if request.method == 'POST':
        form = PayslipAdjustForm(request.POST, instance=payslip)
        if form.is_valid():
            form.save()
            messages.success(request, 'Bonus and deductions saved.')
            return _payroll_redirect(payslip.year, payslip.month)
    else:
        form = PayslipAdjustForm(instance=payslip)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': f'Adjust payslip: {_name(payslip.user)}', 'back_url': 'payroll',
        'help': f'{month_label(payslip.year, payslip.month)}. Add a one-time bonus or an extra deduction.',
    })


@login_required
@hr_required
def payroll_export(request):
    year, month = _parse_month(request.GET.get('month'))
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="payroll-{_month_key(year, month)}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['Employee', 'Employee ID', 'Month', 'Status', 'Basic', 'HRA', 'Allowances', 'Gross', 'PF', 'Tax',
                     'Other deduction', 'Unpaid days', 'Leave deduction', 'Bonus', 'Extra deduction', 'Net pay'])
    for s in Payslip.objects.filter(year=year, month=month).select_related('user', 'user__profile'):
        profile = getattr(s.user, 'profile', None)
        writer.writerow([
            _csv_safe(_name(s.user)), _csv_safe(profile.employee_id if profile and profile.employee_id else ''),
            _month_key(year, month), s.get_status_display(), s.basic, s.hra, s.allowances, s.gross, s.pf, s.tax,
            s.other_deduction, s.unpaid_days, s.leave_deduction, s.bonus, s.extra_deduction, s.net,
        ])
    return response


@login_required
@hr_required
@require_POST
def holiday_add(request):
    form = HolidayForm(request.POST)
    if form.is_valid():
        holiday = form.save()
        messages.success(request, f'Added {holiday.name}.')
        return redirect(reverse('holiday_list') + f'?year={holiday.date.year}')
    messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
    return redirect('holiday_list')


@login_required
@hr_required
@require_POST
def holiday_delete(request, pk):
    holiday = get_object_or_404(Holiday, pk=pk)
    year = holiday.date.year
    holiday.delete()
    messages.success(request, 'Holiday removed.')
    return redirect(reverse('holiday_list') + f'?year={year}')


@login_required
@hr_required
def department_list(request):
    if request.method == 'POST':
        form = DepartmentForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Department added.')
            return redirect('department_list')
    else:
        form = DepartmentForm()
    return render(request, 'core/departments.html', {
        'departments': Department.objects.annotate(people=Count('members')),
        'form': form,
    })


@login_required
@hr_required
@require_POST
def department_delete(request, pk):
    department = get_object_or_404(Department, pk=pk)
    department.delete()  # employees ka department khali ho jata hai
    messages.success(request, 'Department deleted.')
    return redirect('department_list')


@login_required
@hr_required
def leave_policy(request):
    current = policies()
    rows = [
        {'kind': kind, 'label': label, 'days': current[kind][0], 'paid': current[kind][1]}
        for kind, label in LeaveRequest.KIND_CHOICES
    ]
    if request.method == 'POST':
        parsed, errors = {}, []
        for row in rows:
            raw = (request.POST.get(f'days_{row["kind"]}') or '').strip()
            try:
                days = int(raw)
                if not 0 <= days <= 366:
                    raise ValueError
            except ValueError:
                errors.append(f'{row["label"]}: enter a whole number from 0 to 366.')
                continue
            parsed[row['kind']] = (days, request.POST.get(f'paid_{row["kind"]}') == 'on')
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            for kind, (days, paid) in parsed.items():
                LeavePolicy.objects.update_or_create(kind=kind, defaults={'days_per_year': days, 'is_paid': paid})
            messages.success(request, 'Leave policy saved.')
            return redirect('leave_policy')
    return render(request, 'core/leave_policy.html', {'rows': rows, 'defaults': DEFAULT_POLICY})
