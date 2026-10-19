"""Har employee ka apna Home: uska kaam, uski attendance, leave, announcements."""
import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from .celebrations import upcoming
from .hr import leave_balance
from .models import Announcement, Attendance, Holiday, LeaveRequest, Payslip, Task
from .permissions import visible_tasks
from .views import office_todos


@login_required
def home(request):
    user = request.user
    today = timezone.localdate()

    mine = visible_tasks(Task.objects.filter(assigned_to=user), user).exclude(status='Completed')
    overdue = mine.filter(due_date__lt=today).count()
    my_tasks = list(mine.select_related('project').order_by('due_date', 'id')[:6])

    announcements = list(
        Announcement.objects.filter(Q(expires_on__isnull=True) | Q(expires_on__gt=today))[:3]
    )
    balances = [row for row in leave_balance(user, today.year) if row['quota']]
    pending_leaves = LeaveRequest.objects.filter(user=user, status='pending').count()
    on_leave = LeaveRequest.objects.filter(
        user=user, status='approved', start_date__lte=today, end_date__gte=today).exists()

    context = {
        'page_title': 'Home',
        'today': today,
        'record': Attendance.objects.filter(user=user, date=today).first(),
        'my_tasks': my_tasks,
        'open_count': mine.count(),
        'overdue_count': overdue,
        'announcements': announcements,
        'balances': balances,
        'pending_leaves': pending_leaves,
        'on_leave': on_leave,
        'holidays': list(Holiday.objects.filter(date__gte=today)[:3]),
        'celebrations': upcoming(today, 7)[:5],
        'todos': office_todos(user),
        'payslip': Payslip.objects.filter(user=user, status='published').order_by('-year', '-month').first(),
        'week_end': today + datetime.timedelta(days=7),
    }
    return render(request, 'core/home.html', context)
