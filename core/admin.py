from django.contrib import admin
from .models import Task

# Register your models here.
@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display=['id','title','description','assigned_to','progress','issue_image','status','priority','progress','due_date','created_by','created_at']