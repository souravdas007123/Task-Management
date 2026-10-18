"""Roles aur permissions.

- admin   : superuser ya UserProfile.role == 'admin'. Sab kuch kar sakta hai.
- manager : sab tasks edit/delete kar sakta hai, projects aur labels manage karta hai.
- member  : sirf apne (assigned ya khud ke banaye) tasks edit karta hai, sirf apne banaye tasks delete karta hai.

Jis user ka profile nahi hai wo 'member' maana jata hai.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import Project


def role_of(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    if user.is_superuser:
        return 'admin'
    profile = getattr(user, 'profile', None)
    return profile.role if profile else 'member'


def is_manager(user):
    return role_of(user) in ('admin', 'manager')


def visible_tasks(queryset, user):
    """Private project ke tasks sirf members/managers/assignee/creator ko; baaki sab ko sab dikhta hai."""
    if is_manager(user):
        return queryset
    # Subquery use kiya hai (join nahi) taaki rows duplicate na hon aur counts sahi rahein
    member_projects = Project.objects.filter(members=user).values('pk')
    return queryset.filter(
        Q(project__isnull=True)
        | Q(project__is_private=False)
        | Q(project_id__in=member_projects)
        | Q(assigned_to=user)
        | Q(created_by=user)
    )


def visible_projects(queryset, user):
    if is_manager(user):
        return queryset
    member_projects = Project.objects.filter(members=user).values('pk')
    return queryset.filter(Q(is_private=False) | Q(pk__in=member_projects))


def can_view_task(user, task):
    if not getattr(user, 'is_authenticated', False):
        return False
    if is_manager(user) or not task.project_id:
        return True
    if task.assigned_to_id == user.id or task.created_by_id == user.id:
        return True
    project = task.project
    return (not project.is_private) or project.members.filter(pk=user.pk).exists()


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
