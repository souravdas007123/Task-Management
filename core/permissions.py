"""Roles aur permissions.

- admin   : superuser ya UserProfile.role == 'admin'. Sab kuch kar sakta hai.
- manager : sab tasks edit/delete kar sakta hai, projects aur labels manage karta hai.
- member  : sirf apne (assigned ya khud ke banaye) tasks edit karta hai, sirf apne banaye tasks delete karta hai.

Jis user ka profile nahi hai wo 'member' maana jata hai.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import Project, Task


def role_of(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    if user.is_superuser:
        return 'admin'
    profile = getattr(user, 'profile', None)
    return profile.role if profile else 'member'


def is_manager(user):
    return role_of(user) in ('admin', 'manager')


def is_hr(user):
    """Salary aur employee records: sirf admin aur HR role. Manager bhi nahi."""
    return role_of(user) in ('admin', 'hr')


def visible_tasks(queryset, user):
    """Manager/admin ko sab tasks. Employee ko sirf apne (assign/banaye) tasks aur apne project ke tasks."""
    if is_manager(user):
        return queryset
    member_projects = Project.objects.filter(members=user).values('pk')
    return queryset.filter(Q(assigned_to=user) | Q(created_by=user) | Q(project_id__in=member_projects))


def visible_projects(queryset, user):
    """Manager/admin ko sab projects. Employee ko sirf wo jinka wo member hai ya jisme uske tasks hain."""
    if is_manager(user):
        return queryset
    own = Task.objects.filter(Q(assigned_to=user) | Q(created_by=user), project__isnull=False).values('project')
    return queryset.filter(Q(members=user) | Q(pk__in=own)).distinct()


def selectable_projects(queryset, user):
    """Form ke dropdown ke liye: expense/task kisi bhi khule project par dala ja sakta hai, private par sirf member."""
    if is_manager(user):
        return queryset
    return queryset.filter(Q(is_private=False) | Q(members=user)).distinct()


def can_view_task(user, task):
    if not getattr(user, 'is_authenticated', False):
        return False
    if is_manager(user) or task.assigned_to_id == user.id or task.created_by_id == user.id:
        return True
    return bool(task.project_id) and task.project.members.filter(pk=user.pk).exists()


def can_edit_task(user, task):
    if not getattr(user, 'is_authenticated', False):
        return False
    return is_manager(user) or task.assigned_to_id == user.id or task.created_by_id == user.id


def can_delete_task(user, task):
    if not getattr(user, 'is_authenticated', False):
        return False
    return is_manager(user) or task.created_by_id == user.id


def hr_required(view):
    """Sirf admin/HR ke liye; baaki ko 403."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_hr(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def manager_required(view):
    """Sirf admin/manager ke liye; baaki ko 403."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_manager(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper
