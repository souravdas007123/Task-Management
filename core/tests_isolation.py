"""Employee (member) ko sirf apna data dikhna chahiye, doosre ka nahi."""
import datetime
import json

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Advance, AttendanceRequest, Claim, Expense, LeaveRequest, Notification, Project, PurchaseRequest, Task, Ticket,
    UserProfile, Visitor,
)

PW = 'Zx9-longer-pass-77'


def make(name, role='member'):
    user = User.objects.create_user(name, f'{name}@x.com', PW)
    UserProfile.objects.update_or_create(user=user, defaults={'role': role})
    return user


class IsolationTests(TestCase):
    def setUp(self):
        self.alice, self.bob, self.mgr = make('alice'), make('bob'), make('mgr', 'manager')
        day = timezone.localdate()
        self.project = Project.objects.create(name='OpenProj')
        self.bob_task = Task.objects.create(title='BOB-SECRET-TASK', assigned_to=self.bob, created_by=self.bob, project=self.project)
        self.mgr_task = Task.objects.create(title='MGR-ONLY-TASK', assigned_to=self.mgr, created_by=self.mgr)
        self.own_task = Task.objects.create(title='ALICE-OWN-TASK', assigned_to=self.alice, created_by=self.mgr)
        LeaveRequest.objects.create(user=self.bob, start_date=day, end_date=day, reason='BOB-LEAVE-REASON')
        Claim.objects.create(user=self.bob, title='BOB-CLAIM', amount=10, receipt=ContentFile(b'x', 'r.pdf'))
        Ticket.objects.create(subject='BOB-TICKET', description='d', raised_by=self.bob)
        Visitor.objects.create(name='BOB-VISITOR', host=self.bob, expected_on=day)
        Advance.objects.create(user=self.bob, amount=500, reason='BOB-ADVANCE')
        PurchaseRequest.objects.create(requested_by=self.bob, title='BOB-PURCHASE', unit_cost=5)
        AttendanceRequest.objects.create(user=self.bob, date=day, reason='BOB-ATT-REASON')
        Expense.objects.create(project=self.project, title='BOB-EXPENSE', amount=5, submitted_by=self.bob,
                               incurred_on=day, receipt=ContentFile(b'x', 'e.pdf'))
        self.client.login(username='alice', password=PW)

    def test_pages_show_no_one_elses_data(self):
        names = ['dashboard', 'board', 'task_calendar', 'timeline', 'task_report', 'analytics', 'home', 'leave_list',
                 'claim_list', 'ticket_list', 'visitor_list', 'advance_list', 'purchase_list', 'request_list',
                 'expense_list', 'attendance_me', 'project_list', 'notifications', 'my_payslips', 'vault_me']
        for name in names:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)
            text = r.content.decode()
            for secret in ('BOB-', 'MGR-ONLY'):
                self.assertNotIn(secret, text, f'{name} leaks {secret}')

    def test_own_task_visible_and_others_hidden(self):
        r = self.client.get(reverse('dashboard'))
        self.assertContains(r, 'ALICE-OWN-TASK')
        self.assertEqual(r.context['total_tasks'], 1)
        self.assertEqual(self.client.get(reverse('task_detail', args=[self.bob_task.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('task_detail', args=[self.mgr_task.pk])).status_code, 404)

    def test_api_and_calendar_feed_scoped(self):
        data = json.loads(self.client.get(reverse('api_task_list')).content)
        self.assertEqual(data['count'], 1)
        self.assertNotContains(self.client.get(reverse('calendar_ics')), 'BOB-SECRET')

    def test_team_pages_closed_to_employee(self):
        for name in ('workload', 'attendance_team', 'budget_overview', 'audit_log', 'signup_list', 'hr_employees',
                     'payroll', 'vault_overview', 'exit_list'):
            self.assertIn(self.client.get(reverse(name)).status_code, (403, 404), name)

    def test_other_users_records_not_openable(self):
        claim = Claim.objects.get()
        for url in (reverse('payslip_detail', args=[1]),):
            self.assertIn(self.client.get(url).status_code, (403, 404))
        self.client.post(reverse('claim_approve', args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'submitted')

    def test_manager_still_sees_everything(self):
        self.client.login(username='mgr', password=PW)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.context['total_tasks'], 3)

    def test_sidebar_hides_workload_for_employee(self):
        self.assertNotContains(self.client.get(reverse('home')), reverse('workload'))
        self.client.login(username='mgr', password=PW)
        self.assertContains(self.client.get(reverse('home')), reverse('workload'))
