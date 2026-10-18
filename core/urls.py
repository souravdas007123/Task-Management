from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, views_api, views_misc, views_projects, views_reports

urlpatterns = [
    # Tasks
    path('', views.dashboard, name='dashboard'),
    path('add/', views.add_task, name='add_task'),
    path('update/<int:pk>/', views.update_task, name='update_task'),
    path('delete/<int:pk>/', views.delete_task, name='delete_task'),
    path('task/<int:pk>/', views.task_detail, name='task_detail'),

    # Task ke andar: comments, checklist, files, time
    path('task/<int:pk>/comment/', views.add_comment, name='add_comment'),
    path('comment/<int:pk>/delete/', views.delete_comment, name='delete_comment'),
    path('task/<int:pk>/checklist/add/', views.add_checklist_item, name='add_checklist_item'),
    path('checklist/<int:pk>/toggle/', views.toggle_checklist_item, name='toggle_checklist_item'),
    path('checklist/<int:pk>/delete/', views.delete_checklist_item, name='delete_checklist_item'),
    path('task/<int:pk>/attach/', views.add_attachment, name='add_attachment'),
    path('attachment/<int:pk>/delete/', views.delete_attachment, name='delete_attachment'),
    path('task/<int:pk>/time/', views.add_time_entry, name='add_time_entry'),
    path('time/<int:pk>/delete/', views.delete_time_entry, name='delete_time_entry'),

    # Views
    path('board/', views_misc.board, name='board'),
    path('calendar/', views_misc.task_calendar, name='task_calendar'),
    path('calendar.ics', views_reports.calendar_ics, name='calendar_ics'),

    # Projects aur labels
    path('projects/', views_projects.project_list, name='project_list'),
    path('projects/add/', views_projects.project_add, name='project_add'),
    path('projects/<int:pk>/edit/', views_projects.project_edit, name='project_edit'),
    path('projects/<int:pk>/delete/', views_projects.project_delete, name='project_delete'),
    path('labels/', views_projects.label_list, name='label_list'),
    path('labels/add/', views_projects.label_add, name='label_add'),
    path('labels/<int:pk>/delete/', views_projects.label_delete, name='label_delete'),

    # Reports
    path('report/', views_reports.task_report, name='task_report'),
    path('analytics/', views_reports.analytics, name='analytics'),

    # Notifications
    path('notifications/', views_misc.notifications, name='notifications'),
    path('notifications/read/', views_misc.notifications_mark_read, name='notifications_mark_read'),
    path('notifications/<int:pk>/open/', views_misc.notification_open, name='notification_open'),

    # JSON API
    path('api/tasks/', views_api.api_task_list, name='api_task_list'),
    path('api/tasks/<int:pk>/', views_api.api_task_detail, name='api_task_detail'),
    path('api/tasks/<int:pk>/status/', views_api.api_task_status, name='api_task_status'),

    # Auth
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
]
