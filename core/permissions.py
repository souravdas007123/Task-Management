"""Roles aur permissions.

- admin   : superuser ya UserProfile.role == 'admin'. Sab kuch kar sakta hai.
- manager : sab tasks edit/delete kar sakta hai, projects aur labels manage karta hai.
- member  : sirf apne (assigned ya khud ke banaye) tasks edit karta hai, sirf apne banaye tasks delete karta hai.

Jis user ka profile nahi hai wo 'member' maana jata hai.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied


def role_of(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    if user.is_superuser:
        return 'admin'
    profile = getattr(user, 'profile', None)
    return profile.role if profile else 'member'


def is_manager(user):
    return role_of(user) in ('admin', 'manager')


def can_edit_task(user, task):
    if not getattr(user, 'is_authenticated', False):
        return False
    return is_manager(user) or task.assigned_to_id == user.id or task.created_by_id == user.id


def can_delete_task(user, task):
    if not getattr(user, 'is_authenticated', False):
        return False
    return is_manager(user) or task.created_by_id == user.id


def manager_required(view):
    """Sirf admin/manager ke liye; baaki ko 403."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_manager(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper
