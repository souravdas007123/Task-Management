from .models import Notification
from .permissions import is_manager, role_of


def app_context(request):
    """Sidebar ke liye: unread notifications aur user ka role (har template mein available)."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    return {
        'unread_notifications_count': Notification.objects.filter(user=user, is_read=False).count(),
        'user_role': role_of(user),
        'user_is_manager': is_manager(user),
    }
