from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import (
    views, views_api, views_attendance, views_budget, views_hr, views_misc, views_office, views_projects, views_reports,
    views_work, views_workplace,
)

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

    # Work management
    path('workload/', views_work.workload, name='workload'),
    path('timeline/', views_work.timeline, name='timeline'),

    # Budget aur expenses
    path('budget/', views_budget.budget_overview, name='budget_overview'),
    path('budget/project/<int:pk>/', views_budget.project_budget, name='project_budget'),
    path('expenses/', views_budget.expense_list, name='expense_list'),
    path('expenses/add/', views_budget.expense_add, name='expense_add'),
    path('expenses/export/', views_budget.expense_export, name='expense_export'),
    path('expenses/<int:pk>/approve/', views_budget.expense_approve, name='expense_approve'),
    path('expenses/<int:pk>/reject/', views_budget.expense_reject, name='expense_reject'),
    path('expenses/<int:pk>/delete/', views_budget.expense_delete, name='expense_delete'),

    # Office: leave aur audit log
    path('leave/', views_office.leave_list, name='leave_list'),
    path('leave/add/', views_office.leave_add, name='leave_add'),
    path('leave/<int:pk>/approve/', views_office.leave_approve, name='leave_approve'),
    path('leave/<int:pk>/reject/', views_office.leave_reject, name='leave_reject'),
    path('leave/<int:pk>/cancel/', views_office.leave_cancel, name='leave_cancel'),
    path('audit/', views_office.audit_log, name='audit_log'),

    # Attendance
    path('attendance/', views_attendance.attendance_me, name='attendance_me'),
    path('attendance/check-in/', views_attendance.check_in, name='check_in'),
    path('attendance/check-out/', views_attendance.check_out, name='check_out'),
    path('attendance/team/', views_attendance.attendance_team, name='attendance_team'),
    path('attendance/team/export/', views_attendance.attendance_team_export, name='attendance_team_export'),
    path('attendance/team/<int:user_id>/', views_attendance.attendance_person, name='attendance_person'),
    path('attendance/edit/<int:user_id>/<str:day>/', views_attendance.attendance_edit, name='attendance_edit'),
    path('attendance/<int:pk>/delete/', views_attendance.attendance_delete, name='attendance_delete'),

    # Announcements
    path('announcements/', views_workplace.announcement_list, name='announcement_list'),
    path('announcements/add/', views_workplace.announcement_add, name='announcement_add'),
    path('announcements/<int:pk>/edit/', views_workplace.announcement_edit, name='announcement_edit'),
    path('announcements/<int:pk>/delete/', views_workplace.announcement_delete, name='announcement_delete'),

    # Assets (HR)
    path('hr/assets/', views_workplace.asset_list, name='asset_list'),
    path('hr/assets/add/', views_workplace.asset_add, name='asset_add'),
    path('hr/assets/<int:pk>/', views_workplace.asset_detail, name='asset_detail'),
    path('hr/assets/<int:pk>/edit/', views_workplace.asset_edit, name='asset_edit'),
    path('hr/assets/<int:pk>/assign/', views_workplace.asset_assign, name='asset_assign'),
    path('hr/assets/<int:pk>/return/', views_workplace.asset_return, name='asset_return'),
    path('hr/assets/<int:pk>/status/', views_workplace.asset_status, name='asset_status'),
    path('hr/assets/<int:pk>/delete/', views_workplace.asset_delete, name='asset_delete'),

    # Onboarding / offboarding checklists
    path('hr/checklists/', views_workplace.checklist_list, name='checklist_list'),
    path('hr/checklists/start/', views_workplace.checklist_start, name='checklist_start'),
    path('hr/checklists/templates/', views_workplace.checklist_templates, name='checklist_templates'),
    path('hr/checklists/<int:pk>/delete/', views_workplace.checklist_delete, name='checklist_delete'),
    path('checklists/<int:pk>/', views_workplace.checklist_detail, name='checklist_detail'),
    path('checklists/task/<int:pk>/toggle/', views_workplace.checklist_toggle, name='checklist_toggle'),

    # People, holidays, payslips (sabke liye)
    path('people/', views_hr.people, name='people'),
    path('people/org/', views_hr.org_chart, name='org_chart'),
    path('me/', views_hr.my_profile, name='my_profile'),
    path('holidays/', views_hr.holiday_list, name='holiday_list'),
    path('payslips/', views_hr.my_payslips, name='my_payslips'),
    path('payslips/<int:pk>/', views_hr.payslip_detail, name='payslip_detail'),

    # HR (sirf admin/HR role)
    path('hr/', views_hr.hr_employees, name='hr_employees'),
    path('hr/employee/<int:user_id>/', views_hr.employee_edit, name='employee_edit'),
    path('hr/salary/<int:user_id>/', views_hr.salary_history, name='salary_history'),
    path('hr/salary/structure/<int:pk>/delete/', views_hr.salary_delete, name='salary_delete'),
    path('hr/payroll/', views_hr.payroll, name='payroll'),
    path('hr/payroll/generate/', views_hr.payroll_generate, name='payroll_generate'),
    path('hr/payroll/publish-all/', views_hr.payroll_publish_all, name='payroll_publish_all'),
    path('hr/payroll/export/', views_hr.payroll_export, name='payroll_export'),
    path('hr/payslip/<int:pk>/publish/', views_hr.payslip_publish, name='payslip_publish'),
    path('hr/payslip/<int:pk>/reopen/', views_hr.payslip_reopen, name='payslip_reopen'),
    path('hr/payslip/<int:pk>/recalculate/', views_hr.payslip_recalculate, name='payslip_recalculate'),
    path('hr/payslip/<int:pk>/delete/', views_hr.payslip_delete, name='payslip_delete'),
    path('hr/payslip/<int:pk>/adjust/', views_hr.payslip_adjust, name='payslip_adjust'),
    path('hr/holidays/add/', views_hr.holiday_add, name='holiday_add'),
    path('hr/holidays/<int:pk>/delete/', views_hr.holiday_delete, name='holiday_delete'),
    path('hr/departments/', views_hr.department_list, name='department_list'),
    path('hr/departments/<int:pk>/delete/', views_hr.department_delete, name='department_delete'),
    path('hr/leave-policy/', views_hr.leave_policy, name='leave_policy'),

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
    path('password/change/', auth_views.PasswordChangeView.as_view(
        template_name='core/auth/password_change.html', success_url=reverse_lazy('password_change_done'),
    ), name='password_change'),
    path('password/change/done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='core/auth/password_change_done.html',
    ), name='password_change_done'),
    path('password/reset/', auth_views.PasswordResetView.as_view(
        template_name='core/auth/password_reset_form.html',
        email_template_name='core/auth/password_reset_email.txt',
        subject_template_name='core/auth/password_reset_subject.txt',
        success_url=reverse_lazy('password_reset_done'),
    ), name='password_reset'),
    path('password/reset/sent/', auth_views.PasswordResetDoneView.as_view(
        template_name='core/auth/password_reset_done.html',
    ), name='password_reset_done'),
    path('password/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='core/auth/password_reset_confirm.html',
        success_url=reverse_lazy('password_reset_complete'),
    ), name='password_reset_confirm'),
    path('password/reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='core/auth/password_reset_complete.html',
    ), name='password_reset_complete'),
]
