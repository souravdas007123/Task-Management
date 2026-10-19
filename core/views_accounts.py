"""Sign up, HR approval, HR ke dwara account banana aur pehle login par password badalna."""
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST

from . import signup as signup_rules
from .forms_accounts import AddEmployeeForm, SignupForm, create_employee
from .models import SignupRequest, UserProfile
from .permissions import hr_required, role_of
from .services import display_name, notify_many


def _client_ip(request):
    return request.META.get('REMOTE_ADDR', 'unknown')


def _hr_users():
    return [u for u in User.objects.filter(is_active=True).select_related('profile') if role_of(u) in ('admin', 'hr')]


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    if not signup_rules.enabled():
        return render(request, 'registration/signup_closed.html', status=403)
    ip = _client_ip(request)
    if request.method == 'POST':
        if signup_rules.throttled(ip):
            return render(request, 'registration/signup_closed.html', {'too_many': True}, status=429)
        form = SignupForm(request.POST)
        if form.is_valid():
            signup_rules.record(ip)
            if form.is_bot:  # bot ko chup-chap "ho gaya" dikha do, account mat banao
                return redirect('signup_done')
            with transaction.atomic():
                user = form.save()
                UserProfile.objects.get_or_create(user=user)
                if not user.is_active:
                    SignupRequest.objects.create(user=user)
            if not user.is_active:
                notify_many(_hr_users(), f'{display_name(user)} signed up and is waiting for approval',
                            link=reverse('signup_list'), request=request)
            return redirect('signup_done')
    else:
        form = SignupForm()
    return render(request, 'registration/signup.html', {'form': form, 'open': signup_rules.mode() == 'open'})


def signup_done(request):
    return render(request, 'registration/signup_done.html', {'open': signup_rules.mode() == 'open'})


@login_required
@hr_required
def signup_list(request):
    pending = SignupRequest.objects.select_related('user')
    return render(request, 'core/signups.html', {'pending': pending, 'page_title': 'Sign up requests'})


@login_required
@hr_required
@require_POST
def signup_approve(request, pk):
    req = get_object_or_404(SignupRequest.objects.select_related('user'), pk=pk)
    user = req.user
    user.is_active = True
    user.save(update_fields=['is_active'])
    req.delete()
    notify_many([user], 'Your account is approved. You can log in now.', link=reverse('home'), request=request)
    messages.success(request, f'Approved {display_name(user)}.')
    return redirect('signup_list')


@login_required
@hr_required
@require_POST
def signup_reject(request, pk):
    req = get_object_or_404(SignupRequest.objects.select_related('user'), pk=pk)
    user = req.user
    name = display_name(user)
    if user.is_active:  # suraksha: chalu account kabhi delete nahi hota
        req.delete()
    else:
        user.delete()
    messages.success(request, f'Rejected the request from {name}.')
    return redirect('signup_list')


@login_required
@hr_required
def employee_add(request):
    can_make_hr = role_of(request.user) == 'admin'
    if request.method == 'POST':
        form = AddEmployeeForm(request.POST, can_make_hr=can_make_hr)
        if form.is_valid():
            user = create_employee(form.cleaned_data)
            messages.success(request, f'Created the account for {display_name(user)}. They must change the password at first login.')
            return redirect('hr_employees')
    else:
        form = AddEmployeeForm(can_make_hr=can_make_hr)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Add employee', 'back_url': 'hr_employees',
        'help': 'Creates a login. Share the temporary password privately; they choose their own at first login.',
    })


class ForcedPasswordChangeView(auth_views.PasswordChangeView):
    template_name = 'core/auth/password_change.html'
    success_url = reverse_lazy('password_change_done')

    def form_valid(self, form):
        response = super().form_valid(form)
        profile = getattr(self.request.user, 'profile', None)
        if profile and profile.must_change_password:
            profile.must_change_password = False
            profile.save(update_fields=['must_change_password'])
        return response
