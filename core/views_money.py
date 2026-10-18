"""Salary advance / loan requests."""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .advances import allocated, balance, emi, is_open, max_amount, repaid
from .forms_office import AdvanceForm
from .hr import month_bounds
from .models import Advance
from .permissions import hr_required, is_hr, role_of
from .services import display_name, notify, notify_many
from .views_requests import hr_users


def _decorate(advance):
    advance.emi = emi(advance)
    advance.repaid = repaid(advance)
    advance.balance = balance(advance)
    advance.open = is_open(advance)
    advance.fully_allocated = advance.status == 'approved' and allocated(advance) >= advance.amount
    return advance


def _parse_month(value, default):
    try:
        year, month = (int(part) for part in (value or '').split('-'))
        month_bounds(year, month)
        if not (2000 <= year <= 2100):
            raise ValueError
        return datetime.date(year, month, 1)
    except (ValueError, TypeError):
        return default


def _next_month(today):
    return datetime.date(today.year + (today.month == 12), today.month % 12 + 1, 1)


@login_required
def advance_list(request):
    hr = is_hr(request.user)
    mine = [_decorate(a) for a in Advance.objects.filter(user=request.user)[:30]]
    queue, running = [], []
    if hr:
        for a in Advance.objects.exclude(user=request.user).select_related('user') if role_of(request.user) != 'admin' \
                else Advance.objects.select_related('user'):
            _decorate(a)
            if a.status == 'pending':
                queue.append(a)
            elif a.status == 'approved' and a.open:
                running.append(a)
    today = timezone.localdate()
    return render(request, 'core/advances.html', {
        'mine': mine, 'queue': queue, 'running': running, 'hr': hr, 'limit': max_amount(request.user),
        'default_month': _next_month(today).strftime('%Y-%m'),
    })


@login_required
def advance_add(request):
    form = AdvanceForm(request.POST or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        advance = form.save(commit=False)
        advance.user = request.user
        advance.save()
        notify_many([u for u in hr_users() if u.pk != request.user.pk],
                    f'{display_name(request.user)} asked for a salary advance of {advance.amount}',
                    link=reverse('advance_list'), actor=request.user, request=request)
        messages.success(request, 'Advance requested. HR will review it.')
        return redirect('advance_list')
    limit = max_amount(request.user)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Request a Salary Advance', 'back_url': 'advance_list',
        'help': 'The money is repaid in equal monthly instalments from your payslip.'
                + (f' You can ask for up to {limit}.' if limit else ''),
    })


def _decide(request, pk, decision):
    advance = get_object_or_404(Advance.objects.select_related('user'), pk=pk)
    if advance.user_id == request.user.id and role_of(request.user) != 'admin':
        messages.error(request, 'Another HR or admin user has to decide on your advance.')
        return redirect('advance_list')
    if advance.status != 'pending':
        messages.error(request, 'This request has already been decided.')
        return redirect('advance_list')
    note = (request.POST.get('note') or '').strip()[:300]
    if decision == 'approved':
        advance.first_month = _parse_month(request.POST.get('first_month'), _next_month(timezone.localdate()))
        if advance.first_month < timezone.localdate().replace(day=1):
            messages.error(request, 'The first deduction cannot be in a past month.')
            return redirect('advance_list')
    advance.status, advance.decided_by, advance.decided_at, advance.review_note = decision, request.user, timezone.now(), note
    advance.save()
    text = f'Your salary advance of {advance.amount} was {decision}'
    if decision == 'approved':
        text += f'. Repayment starts with the {advance.first_month:%B %Y} payslip'
    notify(advance.user, text + (f'. {note}' if note else ''), link=reverse('advance_list'),
           actor=request.user, request=request)
    messages.success(request, f'Advance {decision}.')
    return redirect('advance_list')


@login_required
@hr_required
@require_POST
def advance_approve(request, pk):
    return _decide(request, pk, 'approved')


@login_required
@hr_required
@require_POST
def advance_reject(request, pk):
    return _decide(request, pk, 'rejected')


@login_required
@hr_required
@require_POST
def advance_close(request, pk):
    advance = get_object_or_404(Advance, pk=pk)
    if advance.status != 'approved':
        messages.error(request, 'Only an approved advance can be closed.')
    else:
        advance.status = 'closed'
        advance.save(update_fields=['status'])
        notify(advance.user, 'Your salary advance was closed. No more instalments will be deducted.',
               link=reverse('advance_list'), actor=request.user, request=request)
        messages.success(request, 'Advance closed. Payslips already created keep their deduction.')
    return redirect('advance_list')


@login_required
@require_POST
def advance_cancel(request, pk):
    advance = get_object_or_404(Advance, pk=pk, user=request.user)
    if advance.status != 'pending':
        messages.error(request, 'Only a request that is still waiting can be cancelled.')
    else:
        advance.delete()
        messages.success(request, 'Request cancelled.')
    return redirect('advance_list')
