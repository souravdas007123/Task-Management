"""Purchase requests (do-level approval, order, receive) aur vendors."""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .budget import money, refresh_budget_alert
from .forms_office import PurchaseForm, VendorForm
from .models import Expense, PurchaseRequest, Vendor
from .permissions import hr_required, is_hr, role_of
from .services import display_name, notify, notify_many
from .views_requests import can_review, hr_users, reviewers_for

EXPENSE_CATEGORY = {'equipment': 'hardware', 'software': 'software', 'services': 'services'}


def approval_limit():
    try:
        return Decimal(str(settings.PURCHASE_APPROVAL_LIMIT))
    except InvalidOperation:
        return Decimal('10000')


def can_finance_approve(user, purchase):
    """Doosra level: HR/admin, par wahi nahi jisne pehla level diya, aur khud ki request nahi (admin role ko chhodkar)."""
    if not is_hr(user):
        return False
    if role_of(user) == 'admin':
        return True
    return purchase.level1_by_id != user.id and purchase.requested_by_id != user.id


@login_required
def purchase_list(request):
    user, hr = request.user, is_hr(request.user)
    mine = PurchaseRequest.objects.filter(requested_by=user).select_related('project', 'vendor')[:50]
    level1, level2, work = [], [], []
    pending = PurchaseRequest.objects.filter(status='pending').select_related('requested_by', 'requested_by__profile', 'project', 'vendor')
    for item in pending:
        if can_review(user, item.requested_by):
            level1.append(item)
    if hr:
        for item in PurchaseRequest.objects.filter(status='review').select_related('requested_by', 'project', 'vendor'):
            if can_finance_approve(user, item):
                level2.append(item)
        work = list(PurchaseRequest.objects.filter(status__in=('approved', 'ordered')).select_related('requested_by', 'project', 'vendor'))
    return render(request, 'core/purchases.html', {
        'mine': mine, 'level1': level1, 'level2': level2, 'work': work, 'hr': hr,
        'limit': approval_limit(), 'vendors': Vendor.objects.filter(is_active=True) if hr else [],
        'today': timezone.localdate(),
    })


@login_required
def purchase_add(request):
    form = PurchaseForm(request.POST or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.requested_by = request.user
        item.save()
        notify_many(reviewers_for(request.user), f'{display_name(request.user)} requested: {item.title} ({money(item.total)})',
                    link=reverse('purchase_list'), actor=request.user, request=request)
        messages.success(request, 'Request sent for approval.')
        return redirect('purchase_list')
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Purchase Request', 'back_url': 'purchase_list',
        'help': f'Your manager approves first. Requests above {approval_limit()} also need approval from finance (HR).',
    })


@login_required
@require_POST
def purchase_approve(request, pk):
    with transaction.atomic():
        item = get_object_or_404(PurchaseRequest.objects.select_for_update().select_related('requested_by', 'requested_by__profile'), pk=pk)
        user = request.user
        if item.status == 'pending' and can_review(user, item.requested_by):
            item.level1_by = user
            item.status = 'review' if item.total > approval_limit() else 'approved'
        elif item.status == 'review' and can_finance_approve(user, item):
            item.level2_by = user
            item.status = 'approved'
        else:
            messages.error(request, 'You cannot approve this request right now.')
            return redirect('purchase_list')
        item.save()
    link = reverse('purchase_list')
    if item.status == 'review':
        notify_many([u for u in hr_users() if u.pk != request.user.pk], f'Purchase needs finance approval: {item.title} ({money(item.total)})',
                    link=link, actor=request.user, request=request)
        notify(item.requested_by, f'"{item.title}" was approved by your manager and is now with finance', link=link, actor=request.user, request=request)
    else:
        notify(item.requested_by, f'Your purchase request "{item.title}" was approved', link=link, actor=request.user, request=request)
        notify_many([u for u in hr_users() if u.pk != request.user.pk], f'Ready to order: {item.title}', link=link, actor=request.user, request=request)
    messages.success(request, 'Approved.')
    return redirect('purchase_list')


@login_required
@require_POST
def purchase_reject(request, pk):
    item = get_object_or_404(PurchaseRequest.objects.select_related('requested_by', 'requested_by__profile'), pk=pk)
    allowed = (item.status == 'pending' and can_review(request.user, item.requested_by)) or \
              (item.status == 'review' and can_finance_approve(request.user, item))
    if not allowed:
        messages.error(request, 'You cannot reject this request right now.')
        return redirect('purchase_list')
    note = (request.POST.get('note') or '').strip()[:300]
    item.status, item.review_note = 'rejected', note
    item.save(update_fields=['status', 'review_note'])
    notify(item.requested_by, f'Your purchase request "{item.title}" was rejected' + (f': {note}' if note else ''),
           link=reverse('purchase_list'), actor=request.user, request=request)
    messages.success(request, 'Request rejected.')
    return redirect('purchase_list')


@login_required
@require_POST
def purchase_cancel(request, pk):
    item = get_object_or_404(PurchaseRequest, pk=pk, requested_by=request.user)
    if item.status not in ('pending', 'review', 'approved'):
        messages.error(request, 'This request can no longer be cancelled.')
    else:
        item.status = 'cancelled'
        item.save(update_fields=['status'])
        messages.success(request, 'Request cancelled.')
    return redirect('purchase_list')


@login_required
@hr_required
@require_POST
def purchase_order(request, pk):
    item = get_object_or_404(PurchaseRequest, pk=pk)
    if item.status != 'approved':
        messages.error(request, 'Only an approved request can be ordered.')
        return redirect('purchase_list')
    vendor_id = request.POST.get('vendor')
    if vendor_id and vendor_id.isdigit():
        item.vendor = Vendor.objects.filter(pk=int(vendor_id)).first() or item.vendor
    item.po_number = (request.POST.get('po_number') or '').strip()[:40]
    item.status, item.ordered_on = 'ordered', timezone.localdate()
    item.save()
    notify(item.requested_by, f'"{item.title}" has been ordered', link=reverse('purchase_list'),
           actor=request.user, request=request)
    messages.success(request, 'Marked as ordered.')
    return redirect('purchase_list')


@login_required
@hr_required
@require_POST
def purchase_receive(request, pk):
    with transaction.atomic():
        item = get_object_or_404(PurchaseRequest.objects.select_for_update().select_related('project', 'requested_by'), pk=pk)
        if item.status != 'ordered':
            messages.error(request, 'Only an ordered request can be received.')
            return redirect('purchase_list')
        raw = (request.POST.get('actual_cost') or '').strip()
        try:
            actual = money(Decimal(raw)) if raw else money(item.total)
        except InvalidOperation:
            messages.error(request, 'Enter the real cost as a number.')
            return redirect('purchase_list')
        if actual < Decimal('0.01') or actual > Decimal('9999999999'):
            messages.error(request, 'The cost must be more than zero.')
            return redirect('purchase_list')
        today = timezone.localdate()
        item.status, item.received_on, item.actual_cost = 'received', today, actual
        if item.project_id and item.expense_id is None:
            item.expense = Expense.objects.create(
                project=item.project, title=f'Purchase: {item.title}'[:200], amount=actual,
                category=EXPENSE_CATEGORY.get(item.category, 'other'), incurred_on=today,
                note=f'Purchase request #{item.pk}'[:300], status='approved',
                submitted_by=item.requested_by, reviewed_by=request.user, reviewed_at=timezone.now(),
            )
        item.save()
    if item.project_id:
        refresh_budget_alert(item.project, request)
    notify(item.requested_by, f'"{item.title}" has arrived', link=reverse('purchase_list'),
           actor=request.user, request=request)
    messages.success(request, 'Marked as received.' + (' The cost was added to the project budget.' if item.expense_id else ''))
    return redirect('purchase_list')


# ---------------------------------------------------------------- vendors

@login_required
@hr_required
def vendor_list(request):
    return render(request, 'core/vendors.html', {'vendors': Vendor.objects.all()})


@login_required
@hr_required
def vendor_add(request):
    form = VendorForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Vendor added.')
        return redirect('vendor_list')
    return render(request, 'core/simple_form.html', {'form': form, 'page_title': 'Add Vendor', 'back_url': 'vendor_list'})


@login_required
@hr_required
def vendor_edit(request, pk):
    vendor = get_object_or_404(Vendor, pk=pk)
    form = VendorForm(request.POST or None, instance=vendor)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Vendor updated.')
        return redirect('vendor_list')
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': f'Edit {vendor.name}', 'back_url': 'vendor_list',
        'help': 'Untick "Is active" to hide a vendor from new requests.',
    })
