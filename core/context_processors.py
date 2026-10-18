from django.conf import settings

from .models import Notification
from .permissions import is_hr, is_manager, role_of


def app_context(request):
    """Sidebar ke liye: unread notifications, user ka role aur currency (har template mein available)."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    return {
        'unread_notifications_count': Notification.objects.filter(user=user, is_read=False).count(),
        'user_role': role_of(user),
        'user_is_manager': is_manager(user),
        'user_is_hr': is_hr(user),
        'currency': settings.CURRENCY_SYMBOL,
    }
