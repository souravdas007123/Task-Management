"""Office ke kaam: leave requests (approval ke saath) aur audit log."""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .filters import parse_int
from .forms import LeaveRequestForm
from .models import ActivityLog, LeaveRequest
from .permissions import is_manager, manager_required
from .services import display_name, notify, notify_many

LEAVE_PAGE_SIZE = 20
AUDIT_PAGE_SIZE = 50
AWAY_WINDOW_DAYS = 14


def leave_managers():
    """Leave approve kar sakne wale active users: superuser aur manager/admin role."""
    return [user for user in User.objects.filter(is_active=True) if is_manager(user)]


def away_user_ids(on_date):
    """Un users ki id jinki approved leave `on_date` ko chal rahi hai."""
    return set(
        LeaveRequest.objects.filter(status='approved', start_date__lte=on_date, end_date__gte=on_date)
        .values_list('user_id', flat=True)
    )


def _safe_next(request, default):
    target = request.POST.get('next') or ''
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return default


@login_required
def leave_list(request):
    manager = is_manager(request.user)
    scope = LeaveRequest.objects.select_related('user', 'reviewed_by')
    if not manager:
        scope = scope.filter(user=request.user)

    status = request.GET.get('status')
    if status in dict(LeaveRequest.STATUS_CHOICES):
        scope = scope.filter(status=status)
    else:
        status = ''

    page_obj = Paginator(scope, LEAVE_PAGE_SIZE).get_page(request.GET.get('page'))
    for leave in page_obj.object_list:
        leave.can_cancel = leave.user_id == request.user.id and leave.status == 'pending'
        leave.can_review = manager and (leave.user_id != request.user.id or request.user.is_superuser)

    today = timezone.localdate()
    away = (
        LeaveRequest.objects.filter(
            status='approved', end_date__gte=today,
            start_date__lte=today + datetime.timedelta(days=AWAY_WINDOW_DAYS),
        ).select_related('user').order_by('start_date', 'id')[:30]
    )
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'core/leave.html', {
        'leaves': page_obj,
        'page_obj': page_obj,
        'querystring': query.urlencode(),
        'manager': manager,
        'away': away,
        'away_days': AWAY_WINDOW_DAYS,
        'status_choices': LeaveRequest.STATUS_CHOICES,
        'current_status': status,
        'next_url': request.get_full_path(),
        'today': today,
    })


@login_required
def leave_add(request):
    if request.method == 'POST':
        form = LeaveRequestForm(request.POST)
        if form.is_valid():
            leave = form.save(commit=False)
            leave.user = request.user
            leave.save()
            notify_many(
                leave_managers(),
                f'{display_name(request.user)} asked for leave: {leave.start_date:%d %b} to {leave.end_date:%d %b} ({leave.days} day{"s" if leave.days != 1 else ""})',
                link=reverse('leave_list') + '?status=pending', actor=request.user, request=request,
            )
            messages.success(request, 'Leave request sent. A manager will review it.')
            return redirect('leave_list')
    else:
        form = LeaveRequestForm(initial={'start_date': timezone.localdate(), 'end_date': timezone.localdate()})
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Request Leave', 'back_url': 'leave_list',
        'help': 'A manager approves or rejects it. Approved leave shows on the Workload page.',
    })


def _review(request, pk, new_status):
    leave = get_object_or_404(LeaveRequest.objects.select_related('user'), pk=pk)
    if leave.user_id == request.user.id and not request.user.is_superuser:
        messages.error(request, 'You cannot review your own leave request.')
        return redirect(_safe_next(request, reverse('leave_list')))
    if leave.status != new_status:
        note = (request.POST.get('note') or '').strip()[:300] if new_status == 'rejected' else ''
        leave.status = new_status
        leave.reviewed_by = request.user
        leave.reviewed_at = timezone.now()
        leave.review_note = note
        leave.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])
        word = 'approved' if new_status == 'approved' else 'rejected'
        text = f'Your leave for {leave.start_date:%d %b} to {leave.end_date:%d %b} was {word}'
        if note:
            text += f': {note}'
        notify(leave.user, text, link=reverse('leave_list'), actor=request.user, request=request)
        messages.success(request, f'Leave {word}.')
    return redirect(_safe_next(request, reverse('leave_list')))


@login_required
@manager_required
@require_POST
def leave_approve(request, pk):
    return _review(request, pk, 'approved')


@login_required
@manager_required
@require_POST
def leave_reject(request, pk):
    return _review(request, pk, 'rejected')


@login_required
@require_POST
def leave_cancel(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk, user=request.user)
    if leave.status != 'pending':
        messages.error(request, 'Only a request that is still waiting can be cancelled.')
    else:
        leave.delete()
        messages.success(request, 'Leave request cancelled.')
    return redirect('leave_list')


@login_required
@manager_required
def audit_log(request):
    """Kisne kya badla: saare tasks ki activity ek jagah (sirf manager/admin)."""
    scope = ActivityLog.objects.select_related('actor', 'task')

    actor_id = parse_int(request.GET.get('actor'))
    if actor_id:
        scope = scope.filter(actor_id=actor_id)

    query = (request.GET.get('q') or '').strip()
    if query:
        scope = scope.filter(message__icontains=query)

    date_from = _parse_day(request.GET.get('from'))
    date_to = _parse_day(request.GET.get('to'))
    if date_from:
        scope = scope.filter(created_at__date__gte=date_from)
    if date_to:
        scope = scope.filter(created_at__date__lte=date_to)

    page_obj = Paginator(scope, AUDIT_PAGE_SIZE).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'core/audit_log.html', {
        'entries': page_obj,
        'page_obj': page_obj,
        'querystring': params.urlencode(),
        'users': User.objects.order_by('username'),
        'current': {
            'actor': actor_id, 'q': query,
            'from': date_from.isoformat() if date_from else '',
            'to': date_to.isoformat() if date_to else '',
        },
    })


def _parse_day(value):
    try:
        day = datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return day if 1900 <= day.year <= 2100 else None
