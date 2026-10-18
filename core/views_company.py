"""Company documents/policies aur helpdesk tickets."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms_office import DocumentForm, TicketCommentForm, TicketForm, TicketManageForm
from .models import CompanyDocument, DocumentAck, Ticket, TicketComment
from .permissions import hr_required, is_hr
from .services import display_name, notify, notify_many

PAGE = 25


def hr_users():
    return [u for u in User.objects.filter(is_active=True) if is_hr(u)]


# ---------------------------------------------------------------- documents

@login_required
def document_list(request):
    hr = is_hr(request.user)
    docs = list(CompanyDocument.objects.all())
    done = set(DocumentAck.objects.filter(user=request.user).values_list('document_id', 'version'))
    employees = User.objects.filter(is_active=True).count()
    counts = {}
    if hr:
        for doc_id, version in DocumentAck.objects.filter(
            document__in=[d.pk for d in docs]
        ).values_list('document_id', 'version'):
            counts[(doc_id, version)] = counts.get((doc_id, version), 0) + 1
    pending = 0
    for doc in docs:
        doc.acknowledged = (doc.pk, doc.version) in done
        doc.needs_ack = doc.requires_ack and not doc.acknowledged
        pending += doc.needs_ack
        doc.ack_count = counts.get((doc.pk, doc.version), 0)
    return render(request, 'core/documents.html', {
        'documents': docs, 'hr': hr, 'pending': pending, 'employees': employees,
    })


@login_required
@hr_required
def document_add(request):
    form = DocumentForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        doc = form.save(commit=False)
        doc.uploaded_by = request.user
        doc.save()
        if doc.requires_ack:
            notify_many([u for u in User.objects.filter(is_active=True)],
                        f'Please read and acknowledge: {doc.title}',
                        link=reverse('document_list'), actor=request.user, request=request)
        messages.success(request, 'Document uploaded.')
        return redirect('document_list')
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Upload Document', 'back_url': 'document_list',
        'help': 'PDF, Office files, text or images up to 10 MB.',
    })


@login_required
@hr_required
def document_edit(request, pk):
    doc = get_object_or_404(CompanyDocument, pk=pk)
    form = DocumentForm(request.POST or None, request.FILES or None, instance=doc)
    if request.method == 'POST' and form.is_valid():
        new_file = 'file' in form.changed_data
        item = form.save(commit=False)
        if new_file:
            item.version = doc.version + 1
        item.save()
        if new_file and item.requires_ack:
            notify_many(list(User.objects.filter(is_active=True)),
                        f'New version, please acknowledge again: {item.title}',
                        link=reverse('document_list'), actor=request.user, request=request)
        messages.success(request, 'Document updated.' + (f' Now version {item.version}.' if new_file else ''))
        return redirect('document_list')
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': f'Edit {doc.title}', 'back_url': 'document_list',
        'help': 'Choosing a new file creates the next version and asks everyone to acknowledge again.',
    })


@login_required
@hr_required
@require_POST
def document_delete(request, pk):
    get_object_or_404(CompanyDocument, pk=pk).delete()
    messages.success(request, 'Document deleted.')
    return redirect('document_list')


@login_required
@require_POST
def document_ack(request, pk):
    doc = get_object_or_404(CompanyDocument, pk=pk)
    try:
        with transaction.atomic():
            DocumentAck.objects.get_or_create(document=doc, user=request.user, version=doc.version)
    except IntegrityError:
        pass
    messages.success(request, f'Thanks, you have acknowledged "{doc.title}".')
    return redirect('document_list')


@login_required
@hr_required
def document_acks(request, pk):
    doc = get_object_or_404(CompanyDocument, pk=pk)
    acked = {a.user_id: a for a in DocumentAck.objects.filter(document=doc, version=doc.version)}
    rows = []
    for user in User.objects.filter(is_active=True).order_by('username'):
        rows.append({'user': user, 'ack': acked.get(user.pk)})
    return render(request, 'core/document_acks.html', {
        'doc': doc, 'rows': rows, 'done': len(acked), 'total': len(rows),
    })


# ---------------------------------------------------------------- helpdesk

def _visible_tickets(user):
    qs = Ticket.objects.select_related('raised_by', 'assigned_to')
    return qs if is_hr(user) else qs.filter(Q(raised_by=user) | Q(assigned_to=user))


def _ticket_or_404(user, pk):
    return get_object_or_404(_visible_tickets(user), pk=pk)


@login_required
def ticket_list(request):
    hr = is_hr(request.user)
    tickets = _visible_tickets(request.user)
    scope = request.GET.get('scope')
    if scope == 'mine':
        tickets = tickets.filter(raised_by=request.user)
    elif scope == 'assigned':
        tickets = tickets.filter(assigned_to=request.user)
    else:
        scope = ''
    status = request.GET.get('status')
    if status == 'active':
        tickets = tickets.filter(status__in=('open', 'in_progress'))
    elif status in dict(Ticket.STATUS_CHOICES):
        tickets = tickets.filter(status=status)
    else:
        status = ''
    category = request.GET.get('category')
    if category in dict(Ticket.CATEGORY_CHOICES):
        tickets = tickets.filter(category=category)
    else:
        category = ''
    query = (request.GET.get('q') or '').strip()
    if query:
        tickets = tickets.filter(Q(subject__icontains=query) | Q(raised_by__username__icontains=query))
    page_obj = Paginator(tickets, PAGE).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    base = _visible_tickets(request.user)
    return render(request, 'core/tickets.html', {
        'tickets': page_obj, 'page_obj': page_obj, 'querystring': params.urlencode(), 'hr': hr,
        'current': {'scope': scope, 'status': status, 'category': category, 'q': query},
        'status_choices': Ticket.STATUS_CHOICES, 'category_choices': Ticket.CATEGORY_CHOICES,
        'open_count': base.filter(status__in=('open', 'in_progress')).count(),
        'unassigned': base.filter(status='open', assigned_to__isnull=True).count() if hr else 0,
    })


@login_required
def ticket_add(request):
    form = TicketForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        ticket = form.save(commit=False)
        ticket.raised_by = request.user
        ticket.save()
        notify_many(hr_users(), f'New ticket #{ticket.pk} from {display_name(request.user)}: {ticket.subject}',
                    link=reverse('ticket_detail', args=[ticket.pk]), actor=request.user, request=request)
        messages.success(request, f'Ticket #{ticket.pk} raised. HR will pick it up.')
        return redirect('ticket_detail', pk=ticket.pk)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Raise a Ticket', 'back_url': 'ticket_list',
        'help': 'IT, HR, admin or finance problems and requests. You can follow the replies here.',
    })


@login_required
def ticket_detail(request, pk):
    ticket = _ticket_or_404(request.user, pk)
    hr = is_hr(request.user)
    manage = TicketManageForm(initial={
        'status': ticket.status, 'priority': ticket.priority, 'assigned_to': ticket.assigned_to_id,
    }) if hr else None
    return render(request, 'core/ticket_detail.html', {
        'ticket': ticket, 'hr': hr, 'manage': manage,
        'comments': ticket.comments.select_related('author'),
        'comment_form': TicketCommentForm(),
        'can_close': ticket.raised_by_id == request.user.id and ticket.status != 'closed',
    })


def _others(ticket, actor):
    people = [ticket.raised_by, ticket.assigned_to]
    if ticket.assigned_to_id is None:
        people += hr_users()
    return [p for p in people if p is not None and p.pk != actor.pk]


@login_required
@require_POST
def ticket_comment(request, pk):
    ticket = _ticket_or_404(request.user, pk)
    if ticket.status == 'closed':
        messages.error(request, 'This ticket is closed.')
        return redirect('ticket_detail', pk=pk)
    form = TicketCommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.ticket = ticket
        comment.author = request.user
        comment.save()
        notify_many(_others(ticket, request.user), f'{display_name(request.user)} replied on ticket #{ticket.pk}',
                    link=reverse('ticket_detail', args=[pk]), actor=request.user, request=request)
        # Jawab milne par raiser ka resolved ticket dobara khul jata hai
        if ticket.status == 'resolved' and ticket.raised_by_id == request.user.id:
            ticket.status = 'open'
            ticket.resolved_at = None
            ticket.save(update_fields=['status', 'resolved_at', 'updated_at'])
    else:
        messages.error(request, ' '.join(e for errs in form.errors.values() for e in errs))
    return redirect('ticket_detail', pk=pk)


@login_required
@hr_required
@require_POST
def ticket_manage(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    form = TicketManageForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Invalid values.')
        return redirect('ticket_detail', pk=pk)
    data = form.cleaned_data
    old_status, old_assignee = ticket.status, ticket.assigned_to_id
    ticket.status, ticket.priority, ticket.assigned_to = data['status'], data['priority'], data['assigned_to']
    if ticket.status in ('resolved', 'closed'):
        ticket.resolved_at = ticket.resolved_at or timezone.now()
    else:
        ticket.resolved_at = None
    ticket.save()
    link = reverse('ticket_detail', args=[pk])
    if ticket.status != old_status:
        notify(ticket.raised_by, f'Your ticket #{ticket.pk} is now {ticket.get_status_display().lower()}',
               link=link, actor=request.user, request=request)
    if ticket.assigned_to_id and ticket.assigned_to_id != old_assignee:
        notify(ticket.assigned_to, f'Ticket #{ticket.pk} was assigned to you', link=link,
               actor=request.user, request=request)
    messages.success(request, 'Ticket updated.')
    return redirect('ticket_detail', pk=pk)


@login_required
@require_POST
def ticket_close(request, pk):
    ticket = _ticket_or_404(request.user, pk)
    if ticket.raised_by_id != request.user.id:
        raise PermissionDenied
    if ticket.status != 'closed':
        ticket.status = 'closed'
        ticket.resolved_at = ticket.resolved_at or timezone.now()
        ticket.save(update_fields=['status', 'resolved_at', 'updated_at'])
        messages.success(request, 'Ticket closed.')
    return redirect('ticket_detail', pk=pk)
