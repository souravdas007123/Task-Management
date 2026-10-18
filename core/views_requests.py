"""Reimbursement claims, attendance requests (regularize/WFH) aur resignation/exit."""
import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .advances import balance
from .checklists import start_checklist
from .forms_office import (
    AttendanceRequestForm, ClaimForm, ResignationDecisionForm, ResignationForm,
)
from .models import (
    Advance, AssetAssignment, Attendance, AttendanceRequest, Claim, Resignation, Task, UserProfile,
)
from .permissions import hr_required, is_hr, is_manager, role_of
from .services import display_name, notify, notify_many


def hr_users():
    return [u for u in User.objects.filter(is_active=True) if is_hr(u)]


def can_review(viewer, target):
    """HR/admin sab ka, manager sirf apni direct reports ka. Apna khud ka kabhi nahi (admin role ko chhodkar)."""
    if viewer.pk == target.pk and role_of(viewer) != 'admin':
        return False
    if is_hr(viewer):
        return True
    if is_manager(viewer):
        profile = getattr(target, 'profile', None)
        return bool(profile and profile.reporting_manager_id == viewer.pk)
    return False


def reviewers_for(user):
    """Request aane par kise batana hai: reporting manager (agar review kar sakta hai), warna HR."""
    profile = getattr(user, 'profile', None)
    boss = profile.reporting_manager if profile else None
    if boss is not None and boss.is_active and boss.pk != user.pk and (is_manager(boss) or is_hr(boss)):
        return [boss]
    return [u for u in hr_users() if u.pk != user.pk]


def review_scope(viewer, queryset):
    """Queryset ko un logon tak seemit karta hai jinhe viewer review kar sakta hai (apne ko chhodkar)."""
    if is_hr(viewer):
        return queryset.exclude(user=viewer) if role_of(viewer) != 'admin' else queryset
    if is_manager(viewer):
        return queryset.filter(user__profile__reporting_manager=viewer).exclude(user=viewer)
    return queryset.none()


def _name(user):
    return user.get_full_name() or user.username


# ---------------------------------------------------------------- claims

@login_required
def claim_list(request):
    mine = Claim.objects.filter(user=request.user).select_related('payslip')
    to_review = review_scope(request.user, Claim.objects.filter(status='submitted')).select_related('user')
    approved_unpaid = Claim.objects.filter(user=request.user, status='approved', payslip__isnull=True).aggregate(t=Sum('amount'))['t'] or 0
    for claim in mine:
        claim.can_delete = claim.payslip_id is None and claim.status != 'approved'
    return render(request, 'core/claims.html', {
        'claims': mine[:100], 'to_review': to_review, 'approved_unpaid': approved_unpaid,
        'hr': is_hr(request.user),
    })


@login_required
def claim_add(request):
    form = ClaimForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        claim = form.save(commit=False)
        claim.user = request.user
        claim.save()
        notify_many(reviewers_for(request.user),
                    f'{display_name(request.user)} filed a claim of {claim.amount}: {claim.title}',
                    link=reverse('claim_list'), actor=request.user, request=request)
        messages.success(request, 'Claim filed. You will be told when it is reviewed.')
        return redirect('claim_list')
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'File a Claim', 'back_url': 'claim_list',
        'help': 'Money you spent for work. Once approved it is added to your next payslip.',
    })


def _claim_decide(request, pk, decision):
    claim = get_object_or_404(Claim.objects.select_related('user', 'user__profile'), pk=pk)
    if not can_review(request.user, claim.user):
        messages.error(request, 'You cannot review this claim.')
        return redirect('claim_list')
    note = (request.POST.get('note') or '').strip()[:300]
    if decision == 'approved':
        if claim.status != 'submitted':
            messages.error(request, 'Only a claim that is waiting can be approved.')
            return redirect('claim_list')
    else:
        if claim.payslip_id is not None:
            messages.error(request, 'This claim is already part of a payslip.')
            return redirect('claim_list')
        if claim.status == 'rejected':
            return redirect('claim_list')
    claim.status = decision
    claim.reviewed_by = request.user
    claim.reviewed_at = timezone.now()
    claim.review_note = note
    claim.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])
    notify(claim.user, f'Your claim "{claim.title}" was {decision}' + (f': {note}' if note else ''),
           link=reverse('claim_list'), actor=request.user, request=request)
    messages.success(request, f'Claim {decision}.')
    return redirect('claim_list')


@login_required
@require_POST
def claim_approve(request, pk):
    return _claim_decide(request, pk, 'approved')


@login_required
@require_POST
def claim_reject(request, pk):
    return _claim_decide(request, pk, 'rejected')


@login_required
@require_POST
def claim_delete(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    owner = claim.user_id == request.user.id
    if not (owner or is_hr(request.user)):
        messages.error(request, 'You cannot delete this claim.')
    elif claim.payslip_id is not None or (owner and claim.status == 'approved'):
        messages.error(request, 'An approved claim cannot be deleted.' if claim.payslip_id is None else 'This claim is already part of a payslip.')
    else:
        claim.delete()
        messages.success(request, 'Claim deleted.')
    return redirect('claim_list')


# ---------------------------------------------------------------- attendance requests

@login_required
def request_list(request):
    mine = AttendanceRequest.objects.filter(user=request.user)[:50]
    to_review = review_scope(request.user, AttendanceRequest.objects.filter(status='pending')).select_related('user')
    return render(request, 'core/attendance_requests.html', {'requests': mine, 'to_review': to_review})


@login_required
def request_add(request):
    form = AttendanceRequestForm(request.POST or None, user=request.user,
                                 initial={'date': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.user = request.user
        item.save()
        notify_many(reviewers_for(request.user),
                    f'{display_name(request.user)} sent an attendance request for {item.date:%d %b}',
                    link=reverse('request_list'), actor=request.user, request=request)
        messages.success(request, 'Request sent.')
        return redirect('request_list')
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Attendance Request', 'back_url': 'request_list',
        'help': 'Forgot to check in or out? Fix it here. Or ask to work from home on a day. For work from home leave the times empty.',
    })


def _aware(day, time):
    return timezone.make_aware(datetime.datetime.combine(day, time))


def _apply_request(item, reviewer):
    """Approve hone par attendance record banata/badalta hai. Galat ho to message (str) return, warna None."""
    if item.kind == 'wfh':
        record = Attendance.objects.filter(user=item.user, date=item.date).first()
        if record is not None:
            record.mode = 'wfh'
            record.save(update_fields=['mode'])
        return None
    record = Attendance.objects.select_for_update().filter(user=item.user, date=item.date).first()
    if record is not None and record.check_out is not None:
        return 'That day already has a complete attendance entry. HR can edit it.'
    check_in, check_out = _aware(item.date, item.check_in_time), _aware(item.date, item.check_out_time)
    if record is None:
        Attendance.objects.create(
            user=item.user, date=item.date, check_in=check_in, check_out=check_out,
            note=f'Fixed on request: {item.reason}'[:200], edited_by=reviewer,
        )
    else:
        record.check_in, record.check_out, record.edited_by = check_in, check_out, reviewer
        record.note = f'Fixed on request: {item.reason}'[:200]
        record.save()
    return None


def _request_decide(request, pk, decision):
    item = get_object_or_404(AttendanceRequest.objects.select_related('user', 'user__profile'), pk=pk)
    if not can_review(request.user, item.user):
        messages.error(request, 'You cannot review this request.')
        return redirect('request_list')
    if item.status != 'pending':
        messages.error(request, 'This request has already been reviewed.')
        return redirect('request_list')
    note = (request.POST.get('note') or '').strip()[:300]
    with transaction.atomic():
        if decision == 'approved':
            problem = _apply_request(item, request.user)
            if problem:
                messages.error(request, problem)
                return redirect('request_list')
        item.status, item.reviewed_by, item.reviewed_at, item.review_note = decision, request.user, timezone.now(), note
        item.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])
    notify(item.user, f'Your attendance request for {item.date:%d %b} was {decision}' + (f': {note}' if note else ''),
           link=reverse('request_list'), actor=request.user, request=request)
    messages.success(request, f'Request {decision}.')
    return redirect('request_list')


@login_required
@require_POST
def request_approve(request, pk):
    return _request_decide(request, pk, 'approved')


@login_required
@require_POST
def request_reject(request, pk):
    return _request_decide(request, pk, 'rejected')


@login_required
@require_POST
def request_cancel(request, pk):
    item = get_object_or_404(AttendanceRequest, pk=pk, user=request.user)
    if item.status != 'pending':
        messages.error(request, 'Only a request that is still waiting can be cancelled.')
    else:
        item.delete()
        messages.success(request, 'Request cancelled.')
    return redirect('request_list')


# ---------------------------------------------------------------- resignation and exit

def active_resignation(user):
    return Resignation.objects.filter(user=user, status__in=('pending', 'accepted')).first()


@login_required
def resign(request):
    current = active_resignation(request.user)
    if request.method == 'POST':
        if current is not None:
            messages.error(request, 'You already have a resignation in progress.')
            return redirect('resign')
        form = ResignationForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.user = request.user
            try:
                with transaction.atomic():
                    item.save()
            except IntegrityError:
                messages.error(request, 'You already have a resignation in progress.')
                return redirect('resign')
            notify_many([u for u in hr_users() if u.pk != request.user.pk],
                        f'{display_name(request.user)} has submitted a resignation',
                        link=reverse('exit_list'), actor=request.user, request=request)
            messages.success(request, 'Resignation submitted. HR will confirm your last working day.')
            return redirect('resign')
    else:
        form = ResignationForm()
    return render(request, 'core/resign.html', {
        'current': current, 'form': form, 'history': Resignation.objects.filter(user=request.user)[:10],
    })


@login_required
@require_POST
def resign_withdraw(request):
    item = active_resignation(request.user)
    if item is None:
        messages.error(request, 'You have no resignation to withdraw.')
        return redirect('resign')
    item.status = 'withdrawn'
    item.save(update_fields=['status'])
    notify_many([u for u in hr_users() if u.pk != request.user.pk],
                f'{display_name(request.user)} withdrew the resignation',
                link=reverse('exit_list'), actor=request.user, request=request)
    messages.success(request, 'Resignation withdrawn.')
    return redirect('resign')


@login_required
@hr_required
def exit_list(request):
    active = Resignation.objects.filter(status__in=('pending', 'accepted')).select_related('user', 'user__profile')
    recent = Resignation.objects.exclude(status__in=('pending', 'accepted')).select_related('user')[:20]
    today = timezone.localdate()
    rows = []
    for item in active:
        item.assets = AssetAssignment.objects.filter(user=item.user, returned_on__isnull=True).count()
        item.advance_balance = sum((balance(a) for a in Advance.objects.filter(user=item.user, status='approved')), Decimal('0'))
        item.open_tasks = Task.objects.filter(assigned_to=item.user).exclude(status='Completed').count()
        item.ready = item.status == 'accepted' and item.last_working_day and item.last_working_day <= today
        item.own = item.user_id == request.user.id
        rows.append(item)
    return render(request, 'core/exits.html', {'rows': rows, 'recent': recent, 'today': today})


@login_required
@hr_required
@require_POST
def exit_decide(request, pk, decision):
    item = get_object_or_404(Resignation.objects.select_related('user'), pk=pk)
    if item.user_id == request.user.id:
        messages.error(request, 'Another HR or admin user has to decide on your resignation.')
        return redirect('exit_list')
    if item.status != 'pending':
        messages.error(request, 'This resignation has already been decided.')
        return redirect('exit_list')
    form = ResignationDecisionForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Invalid values.')
        return redirect('exit_list')
    note = form.cleaned_data['hr_note'].strip()
    if decision == 'accepted':
        last_day = form.cleaned_data['last_working_day'] or item.proposed_last_day
        if last_day < item.submitted_on:
            messages.error(request, 'The last working day cannot be before the resignation date.')
            return redirect('exit_list')
        item.last_working_day = last_day
    item.status, item.hr_note, item.decided_by, item.decided_at = decision, note, request.user, timezone.now()
    item.save()
    link = reverse('resign')
    if decision == 'accepted':
        start_checklist(item.user, 'offboarding', request.user)
        notify(item.user, f'Your resignation was accepted. Last working day: {item.last_working_day:%d %b %Y}',
               link=link, actor=request.user, request=request)
        messages.success(request, 'Resignation accepted and the offboarding checklist was started.')
    else:
        notify(item.user, 'Your resignation was not accepted' + (f': {note}' if note else ''),
               link=link, actor=request.user, request=request)
        messages.success(request, 'Resignation rejected.')
    return redirect('exit_list')


@login_required
@hr_required
@require_POST
def exit_complete(request, pk):
    item = get_object_or_404(Resignation.objects.select_related('user'), pk=pk)
    user = item.user
    if item.status != 'accepted' or item.last_working_day is None:
        messages.error(request, 'Only an accepted resignation can be completed.')
    elif item.last_working_day > timezone.localdate():
        messages.error(request, f'The last working day is {item.last_working_day:%d %b %Y}. Complete the exit on or after that day.')
    elif user.pk == request.user.pk:
        messages.error(request, 'Another HR or admin user has to complete your exit.')
    elif AssetAssignment.objects.filter(user=user, returned_on__isnull=True).exists():
        messages.error(request, 'Collect the company assets first, then complete the exit.')
    elif user.is_superuser and not User.objects.filter(is_active=True, is_superuser=True).exclude(pk=user.pk).exists():
        messages.error(request, 'This is the only active superuser. Make another one first.')
    else:
        open_tasks = Task.objects.filter(assigned_to=user).exclude(status='Completed').count()
        with transaction.atomic():
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.exit_date = item.last_working_day
            profile.save(update_fields=['exit_date'])
            user.is_active = False
            user.save(update_fields=['is_active'])
            item.status = 'completed'
            item.save(update_fields=['status'])
        extra = f' {open_tasks} open task(s) are still assigned to them.' if open_tasks else ''
        messages.success(request, f'{_name(user)} has left. Their login is disabled.{extra}')
    return redirect('exit_list')
