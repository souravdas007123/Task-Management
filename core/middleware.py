from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse


class PasswordChangeRequiredMiddleware:
    """HR ne jinka account banaya, wo pehle password badle bina kuch nahi dekh sakte."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and self._must_change(user):
            path = request.path
            allowed = (reverse('password_change'), reverse('password_change_done'), reverse('logout'))
            if path not in allowed and not path.startswith(('/static/', '/media/')):
                messages.info(request, 'Please choose a new password before you continue.')
                return redirect('password_change')
        return self.get_response(request)

    @staticmethod
    def _must_change(user):
        profile = getattr(user, 'profile', None)
        return bool(profile and profile.must_change_password)
