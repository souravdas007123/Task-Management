from django.contrib import admin
from .models import Task

# Register your models here.
@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'assigned_to', 'status', 'priority', 'progress', 'due_date', 'issue_image', 'created_by', 'created_at']
    list_filter = ['status', 'priority']
    search_fields = ['title', 'description']