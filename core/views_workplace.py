"""Announcements, assets (laptop wagairah) aur onboarding/offboarding checklists."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .checklists import DEFAULT_TEMPLATES, MAX_ITEMS, parse_items, start_checklist, template_text
from .filters import parse_int
from .forms import AnnouncementForm, AssetAssignForm, AssetForm
from .models import (
    Announcement, Asset, AssetAssignment, ChecklistTask, ChecklistTemplate, EmployeeChecklist, Notification,
)
from .permissions import hr_required, is_hr
from .services import display_name, notify, notify_many


def _name(user):
    return user.get_full_name() or user.username


def hr_users():
    return [u for u in User.objects.filter(is_active=True) if is_hr(u)]


# ---------------------------------------------------------------- announcements

def active_announcements(today=None):
    today = today or timezone.localdate()
    return Announcement.objects.filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today))


@login_required
def announcement_list(request):
    today = timezone.localdate()
    hr = is_hr(request.user)
    items = list(Announcement.objects.select_related('created_by') if hr else active_announcements(today).select_related('created_by'))
    for item in items:
        item.expired = bool(item.expires_on and item.expires_on < today)
    return render(request, 'core/announcements.html', {'announcements': items, 'hr': hr})


@login_required
@hr_required
def announcement_add(request):
    if request.method == 'POST':
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.created_by = request.user
            item.save()
            # Sabko in-app notification (email nahi: bahut logon ko ek saath email dhime ho jate)
            Notification.objects.bulk_create([
                Notification(user=u, message=f'Announcement: {item.title}'[:300], link=reverse('announcement_list'))
                for u in User.objects.filter(is_active=True).exclude(pk=request.user.pk)
            ])
            messages.success(request, 'Announcement posted. Everyone has been notified.')
            return redirect('announcement_list')
    else:
        form = AnnouncementForm()
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'New Announcement', 'back_url': 'announcement_list',
        'help': 'Everyone in the company sees this on the dashboard and the Announcements page.',
    })


@login_required
@hr_required
def announcement_edit(request, pk):
    item = get_object_or_404(Announcement, pk=pk)
    if request.method == 'POST':
        form = AnnouncementForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, 'Announcement updated.')
            return redirect('announcement_list')
    else:
        form = AnnouncementForm(instance=item)
    return render(request, 'core/simple_form.html', {'form': form, 'page_title': 'Edit Announcement', 'back_url': 'announcement_list'})


@login_required
@hr_required
@require_POST
def announcement_delete(request, pk):
    get_object_or_404(Announcement, pk=pk).delete()
    messages.success(request, 'Announcement deleted.')
    return redirect('announcement_list')


# ---------------------------------------------------------------- assets

ASSET_PAGE = 30


@login_required
@hr_required
def asset_list(request):
    assets = Asset.objects.all()
    query = (request.GET.get('q') or '').strip()
    if query:
        assets = assets.filter(
            Q(name__icontains=query) | Q(asset_tag__icontains=query) | Q(serial_number__icontains=query)
            | Q(assignments__returned_on__isnull=True, assignments__user__username__icontains=query)
        ).distinct()
    status = request.GET.get('status')
    if status in dict(Asset.STATUS_CHOICES):
        assets = assets.filter(status=status)
    else:
        status = ''
    category = request.GET.get('category')
    if category in dict(Asset.CATEGORY_CHOICES):
        assets = assets.filter(category=category)
    else:
        category = ''

    page_obj = Paginator(assets, ASSET_PAGE).get_page(request.GET.get('page'))
    holders = {
        a.asset_id: a.user for a in
        AssetAssignment.objects.filter(returned_on__isnull=True, asset__in=list(page_obj.object_list)).select_related('user')
    }
    for asset in page_obj.object_list:
        asset.holder = holders.get(asset.pk)
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'core/assets.html', {
        'assets': page_obj, 'page_obj': page_obj, 'querystring': params.urlencode(),
        'current': {'q': query, 'status': status, 'category': category},
        'status_choices': Asset.STATUS_CHOICES, 'category_choices': Asset.CATEGORY_CHOICES,
    })


@login_required
@hr_required
def asset_add(request):
    if request.method == 'POST':
        form = AssetForm(request.POST)
        if form.is_valid():
            asset = form.save()
            messages.success(request, f'Added {asset.asset_tag}.')
            return redirect('asset_detail', pk=asset.pk)
    else:
        form = AssetForm()
    return render(request, 'core/simple_form.html', {'form': form, 'page_title': 'Add Asset', 'back_url': 'asset_list'})


@login_required
@hr_required
def asset_edit(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    if request.method == 'POST':
        form = AssetForm(request.POST, instance=asset)
        if form.is_valid():
            form.save()
            messages.success(request, 'Asset updated.')
            return redirect('asset_detail', pk=asset.pk)
    else:
        form = AssetForm(instance=asset)
    return render(request, 'core/simple_form.html', {'form': form, 'page_title': f'Edit {asset.asset_tag}', 'back_url': 'asset_list'})


@login_required
@hr_required
def asset_detail(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    current = asset.current_assignment
    return render(request, 'core/asset_detail.html', {
        'asset': asset,
        'current': current,
        'history': asset.assignments.select_related('user', 'assigned_by'),
        'assign_form': AssetAssignForm() if asset.status == 'available' else None,
    })


@login_required
@hr_required
@require_POST
def asset_assign(request, pk):
    form = AssetAssignForm(request.POST)
    if not form.is_valid():
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
        return redirect('asset_detail', pk=pk)
    try:
        with transaction.atomic():
            asset = Asset.objects.select_for_update().get(pk=pk)
            if asset.status != 'available':
                messages.error(request, 'This asset is not available. Return it first.')
                return redirect('asset_detail', pk=pk)
            AssetAssignment.objects.create(
                asset=asset, user=form.cleaned_data['user'], assigned_on=form.cleaned_data['assigned_on'],
                note=form.cleaned_data['note'], assigned_by=request.user,
            )
            asset.status = 'assigned'
            asset.save(update_fields=['status'])
    except IntegrityError:
        messages.error(request, 'Someone else just assigned this asset.')
        return redirect('asset_detail', pk=pk)
    notify(form.cleaned_data['user'], f'{asset.name} ({asset.asset_tag}) was assigned to you',
           link=reverse('my_profile'), actor=request.user, request=request)
    messages.success(request, f'Assigned to {_name(form.cleaned_data["user"])}.')
    return redirect('asset_detail', pk=pk)


@login_required
@hr_required
@require_POST
def asset_return(request, pk):
    with transaction.atomic():
        asset = get_object_or_404(Asset.objects.select_for_update(), pk=pk)
        current = asset.assignments.filter(returned_on__isnull=True).select_related('user').first()
        if current is None:
            messages.error(request, 'This asset is not assigned to anyone.')
            return redirect('asset_detail', pk=pk)
        current.returned_on = max(timezone.localdate(), current.assigned_on)
        current.return_note = (request.POST.get('return_note') or '').strip()[:200]
        current.save(update_fields=['returned_on', 'return_note'])
        asset.status = 'repair' if request.POST.get('needs_repair') == 'on' else 'available'
        asset.save(update_fields=['status'])
    notify(current.user, f'{asset.name} ({asset.asset_tag}) was marked as returned',
           link=reverse('my_profile'), actor=request.user, request=request)
    messages.success(request, 'Marked as returned.')
    return redirect('asset_detail', pk=pk)


@login_required
@hr_required
@require_POST
def asset_status(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    new_status = request.POST.get('status')
    if asset.status == 'assigned':
        messages.error(request, 'Return the asset first.')
    elif new_status in ('available', 'repair', 'retired'):
        asset.status = new_status
        asset.save(update_fields=['status'])
        messages.success(request, f'Marked as {asset.get_status_display().lower()}.')
    return redirect('asset_detail', pk=pk)


@login_required
@hr_required
@require_POST
def asset_delete(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    if asset.assignments.exists():
        messages.error(request, 'This asset has history. Mark it as retired instead of deleting it.')
        return redirect('asset_detail', pk=pk)
    asset.delete()
    messages.success(request, 'Asset deleted.')
    return redirect('asset_list')


# ---------------------------------------------------------------- onboarding / offboarding

def _can_view_checklist(user, checklist):
    if is_hr(user) or checklist.user_id == user.id:
        return True
    profile = getattr(checklist.user, 'profile', None)
    return bool(profile and profile.reporting_manager_id == user.id)


def _can_tick(user, task):
    checklist = task.checklist
    if is_hr(user):
        return True
    if task.owner == 'Employee':
        return checklist.user_id == user.id
    if task.owner == 'Manager':
        profile = getattr(checklist.user, 'profile', None)
        return bool(profile and profile.reporting_manager_id == user.id)
    return False


@login_required
@hr_required
def checklist_list(request):
    status = request.GET.get('status')
    lists = EmployeeChecklist.objects.select_related('user').annotate(
        total=Count('tasks'), done=Count('tasks', filter=Q(tasks__is_done=True)),
    )
    if status == 'open':
        lists = lists.filter(completed_at__isnull=True)
    elif status == 'done':
        lists = lists.filter(completed_at__isnull=False)
    else:
        status = ''
    return render(request, 'core/checklists.html', {
        'checklists': lists,
        'current_status': status,
        'people': User.objects.filter(is_active=True).order_by('username'),
        'kinds': EmployeeChecklist.KIND_CHOICES,
    })


@login_required
@hr_required
@require_POST
def checklist_start(request):
    user_id = parse_int(request.POST.get('user'))
    user = User.objects.filter(pk=user_id, is_active=True).first() if user_id else None
    kind = request.POST.get('kind')
    if user is None or kind not in dict(EmployeeChecklist.KIND_CHOICES):
        messages.error(request, 'Choose a person and a checklist type.')
        return redirect('checklist_list')
    checklist = start_checklist(user, kind, request.user)
    if checklist is None:
        messages.error(request, f'{_name(user)} already has an unfinished {kind} checklist.')
        return redirect('checklist_list')
    notify(user, f'Your {kind} checklist has started', link=reverse('checklist_detail', args=[checklist.pk]),
           actor=request.user, request=request)
    messages.success(request, f'Started the {kind} checklist for {_name(user)}.')
    return redirect('checklist_detail', pk=checklist.pk)


@login_required
def checklist_detail(request, pk):
    checklist = get_object_or_404(EmployeeChecklist.objects.select_related('user__profile'), pk=pk)
    if not _can_view_checklist(request.user, checklist):
        raise Http404
    tasks = list(checklist.tasks.select_related('done_by'))
    for task in tasks:
        task.can_tick = _can_tick(request.user, task)
    done = sum(1 for t in tasks if t.is_done)
    held = AssetAssignment.objects.filter(user=checklist.user, returned_on__isnull=True).select_related('asset')
    return render(request, 'core/checklist_detail.html', {
        'checklist': checklist, 'tasks': tasks, 'done': done, 'total': len(tasks),
        'percent': int(done * 100 / len(tasks)) if tasks else 0,
        'held_assets': held if (is_hr(request.user) or checklist.user_id == request.user.id) else [],
        'hr': is_hr(request.user),
    })


@login_required
@require_POST
def checklist_toggle(request, pk):
    task = get_object_or_404(ChecklistTask.objects.select_related('checklist__user__profile'), pk=pk)
    checklist = task.checklist
    if not _can_view_checklist(request.user, checklist):
        raise Http404
    if not _can_tick(request.user, task):
        messages.error(request, f'Only {task.owner if task.owner != "Employee" else "the employee"} can tick this step.')
        return redirect('checklist_detail', pk=checklist.pk)

    with transaction.atomic():
        task.is_done = not task.is_done
        task.done_by = request.user if task.is_done else None
        task.done_at = timezone.now() if task.is_done else None
        task.save(update_fields=['is_done', 'done_by', 'done_at'])
        all_done = not checklist.tasks.filter(is_done=False).exists()
        was_complete = checklist.completed_at is not None
        if all_done and not was_complete:
            checklist.completed_at = timezone.now()
            checklist.save(update_fields=['completed_at'])
        elif not all_done and was_complete:
            checklist.completed_at = None
            checklist.save(update_fields=['completed_at'])
    if all_done and not was_complete:
        notify_many(hr_users(), f'The {checklist.kind} checklist of {_name(checklist.user)} is complete',
                    link=reverse('checklist_detail', args=[checklist.pk]), actor=request.user, request=request)
    return redirect('checklist_detail', pk=checklist.pk)


@login_required
@hr_required
@require_POST
def checklist_delete(request, pk):
    get_object_or_404(EmployeeChecklist, pk=pk).delete()
    messages.success(request, 'Checklist deleted.')
    return redirect('checklist_list')


@login_required
@hr_required
def checklist_templates(request):
    texts = {kind: template_text(kind) for kind, _label in ChecklistTemplate.KIND_CHOICES}
    errors = []
    if request.method == 'POST':
        parsed = {}
        for kind, label in ChecklistTemplate.KIND_CHOICES:
            texts[kind] = request.POST.get(kind, '')
            items = parse_items(texts[kind])
            if not items:
                errors.append(f'{label}: add at least one step.')
            elif len(items) > MAX_ITEMS:
                errors.append(f'{label}: at most {MAX_ITEMS} steps.')
            parsed[kind] = items
        if not errors:
            for kind, items in parsed.items():
                ChecklistTemplate.objects.update_or_create(
                    kind=kind, defaults={'items': '\n'.join(f'{owner} | {title}' for owner, title in items)},
                )
            messages.success(request, 'Templates saved. They apply to checklists started from now on.')
            return redirect('checklist_templates')
    return render(request, 'core/checklist_templates.html', {
        'sections': [(kind, label, texts[kind]) for kind, label in ChecklistTemplate.KIND_CHOICES],
        'errors': errors,
        'max_items': MAX_ITEMS,
        'defaults': DEFAULT_TEMPLATES,
    })
