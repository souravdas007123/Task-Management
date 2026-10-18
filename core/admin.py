from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import (
    ActivityLog, Attachment, ChecklistItem, Comment, Expense, Label, Notification,
    Project, Task, TimeEntry, UserProfile,
)


class ChecklistInline(admin.TabularInline):
    model = ChecklistItem
    extra = 0


# Register your models here.
@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'project', 'assigned_to', 'status', 'priority', 'progress', 'due_date', 'recurrence', 'created_by', 'created_at']
    list_filter = ['status', 'priority', 'project', 'labels', 'recurrence']
    search_fields = ['title', 'description']
    inlines = [ChecklistInline]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'budget', 'created_by', 'created_at']
    search_fields = ['name']


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ['title', 'project', 'amount', 'category', 'status', 'submitted_by', 'incurred_on']
    list_filter = ['status', 'category', 'project']
    search_fields = ['title', 'note']


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ['name', 'color']


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ['task', 'author', 'created_at']


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ['original_name', 'task', 'uploaded_by', 'uploaded_at']


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = ['task', 'user', 'minutes', 'worked_on']


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ['task', 'actor', 'message', 'created_at']


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['user', 'message', 'is_read', 'created_at']
    list_filter = ['is_read']


class UserProfileInline(admin.StackedInline):
    """User edit page par hi role, hourly rate (labour cost ke liye) aur weekly capacity set karo."""
    model = UserProfile
    can_delete = False


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]
    list_display = BaseUserAdmin.list_display + ('get_role',)

    @admin.display(description='Role')
    def get_role(self, obj):
        if obj.is_superuser:
            return 'Admin (superuser)'
        profile = getattr(obj, 'profile', None)
        return profile.get_role_display() if profile else 'Member'
