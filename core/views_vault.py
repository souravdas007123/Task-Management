"""Employee ke personal documents (PAN, Aadhaar...). Private storage; sirf employee aur HR."""
import datetime
import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms_office import EmployeeDocumentForm
from .models import EmployeeDocument
from .permissions import hr_required, is_hr
from .services import notify

SOON_DAYS = 30


def expiry_state(doc, today):
    if doc.expires_on is None:
        return ''
    if doc.expires_on < today:
        return 'expired'
    if (doc.expires_on - today).days <= SOON_DAYS:
        return 'soon'
    return ''


def _decorate(docs, today):
    docs = list(docs)
    for doc in docs:
        doc.state = expiry_state(doc, today)
    return docs


def _name(user):
    return user.get_full_name() or user.username


@login_required
def vault_me(request):
    today = timezone.localdate()
    return render(request, 'core/vault.html', {
        'person': request.user, 'docs': _decorate(EmployeeDocument.objects.filter(user=request.user), today),
        'own': True, 'hr': is_hr(request.user),
    })


@login_required
@hr_required
def vault_person(request, user_id):
    person = get_object_or_404(User, pk=user_id)
    today = timezone.localdate()
    return render(request, 'core/vault.html', {
        'person': person, 'docs': _decorate(EmployeeDocument.objects.filter(user=person), today),
        'own': person.pk == request.user.pk, 'hr': True,
    })


def _add(request, person):
    form = EmployeeDocumentForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        doc = form.save(commit=False)
        doc.user, doc.uploaded_by = person, request.user
        doc.save()
        if person.pk != request.user.pk:
            notify(person, f'HR added "{doc.label}" to your documents', link=reverse('vault_me'),
                   actor=request.user, request=request)
        messages.success(request, 'Document saved.')
        if person.pk == request.user.pk:
            return redirect('vault_me')
        return redirect('vault_person', user_id=person.pk)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Add a Document' if person.pk == request.user.pk else f'Add a Document for {_name(person)}',
        'back_url': 'vault_me' if person.pk == request.user.pk else 'vault_overview',
        'help': 'Stored privately. Only the employee and HR can open it.',
    })


@login_required
def vault_add(request):
    return _add(request, request.user)


@login_required
@hr_required
def vault_person_add(request, user_id):
    return _add(request, get_object_or_404(User, pk=user_id))


def _owned_or_hr(user, pk):
    doc = get_object_or_404(EmployeeDocument.objects.select_related('user'), pk=pk)
    if doc.user_id != user.id and not is_hr(user):
        raise Http404
    return doc


@login_required
def vault_download(request, pk):
    doc = _owned_or_hr(request.user, pk)
    try:
        handle = doc.file.open('rb')
    except (FileNotFoundError, ValueError, OSError):
        raise Http404
    content_type = mimetypes.guess_type(doc.file.name)[0] or 'application/octet-stream'
    response = FileResponse(handle, as_attachment=True, filename=doc.file.name.rsplit('/', 1)[-1], content_type=content_type)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_POST
def vault_delete(request, pk):
    doc = _owned_or_hr(request.user, pk)
    if doc.is_verified and not is_hr(request.user):
        messages.error(request, 'HR has verified this document. Ask HR to remove it.')
        return redirect('vault_me')
    owner_id = doc.user_id
    doc.file.delete(save=False)
    doc.delete()
    messages.success(request, 'Document deleted.')
    if owner_id != request.user.id:
        return redirect('vault_person', user_id=owner_id)
    return redirect('vault_me')


@login_required
@hr_required
@require_POST
def vault_verify(request, pk):
    doc = get_object_or_404(EmployeeDocument, pk=pk)
    if doc.user_id == request.user.id:
        messages.error(request, 'Another HR or admin user has to verify your document.')
    else:
        doc.is_verified = not doc.is_verified
        doc.verified_by = request.user if doc.is_verified else None
        doc.save(update_fields=['is_verified', 'verified_by'])
        messages.success(request, 'Marked as verified.' if doc.is_verified else 'Verification removed.')
    return redirect('vault_person', user_id=doc.user_id)


@login_required
@hr_required
def vault_overview(request):
    today = timezone.localdate()
    horizon = today + datetime.timedelta(days=60)
    expiring = _decorate(
        EmployeeDocument.objects.filter(expires_on__isnull=False, expires_on__lte=horizon,
                                        user__is_active=True).select_related('user').order_by('expires_on'),
        today,
    )
    return render(request, 'core/vault_overview.html', {'docs': expiring})
