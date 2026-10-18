import datetime
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from .budget import project_budget_summary, refresh_budget_alert
from .models import Expense, Notification, Project, Task, TimeEntry, UserProfile
from .tests_features import Base
from .workload import build_timeline, build_workload


def set_profile(user, **fields):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    for key, value in fields.items():
        setattr(profile, key, value)
    profile.save()


class BudgetBase(Base):
    def setUp(self):
        super().setUp()
        self.project = Project.objects.create(name='Alpha', budget=Decimal('1000'))

    def expense(self, amount, status='approved', **kw):
        defaults = dict(project=self.project, title='Thing', amount=Decimal(amount), status=status,
                        submitted_by=self.owner)
        defaults.update(kw)
        return Expense.objects.create(**defaults)


class BudgetSummaryTests(BudgetBase):
    def test_only_approved_counts(self):
        self.expense('300')
        self.expense('200', status='submitted')
        self.expense('100', status='rejected')
        s = project_budget_summary(self.project)
        self.assertEqual(s['spent'], Decimal('300'))
        self.assertEqual(s['expenses_pending'], Decimal('200'))
        self.assertEqual(s['percent'], 30)
        self.assertEqual(s['state'], 'ok')

    def test_labour_cost_and_unrated(self):
        set_profile(self.owner, hourly_rate=Decimal('100'))
        task = self.task(project=self.project)
        TimeEntry.objects.create(task=task, user=self.owner, minutes=90, worked_on=self.today)
        TimeEntry.objects.create(task=task, user=self.other, minutes=60, worked_on=self.today)
        s = project_budget_summary(self.project)
        self.assertEqual(s['labor_cost'], Decimal('150.00'))
        self.assertEqual(s['unrated_minutes'], 60)
        self.assertEqual(s['spent'], Decimal('150.00'))

    def test_levels(self):
        self.expense('799')
        self.assertEqual(project_budget_summary(self.project)['level'], 0)
        self.expense('1')
        s = project_budget_summary(self.project)
        self.assertEqual((s['level'], s['state']), (80, 'warning'))
        self.expense('200')
        s = project_budget_summary(self.project)
        self.assertEqual((s['level'], s['state']), (100, 'warning'))
        self.expense('1')
        s = project_budget_summary(self.project)
        self.assertEqual(s['state'], 'over')
        self.assertEqual(s['over_by'], Decimal('1'))

    def test_no_budget(self):
        p = Project.objects.create(name='Free')
        s = project_budget_summary(p)
        self.assertFalse(s['has_budget'])
        self.assertEqual(s['state'], 'none')
        self.assertIsNone(s['percent'])


class BudgetAlertTests(BudgetBase):
    def test_alert_once_and_reset(self):
        self.expense('850')
        refresh_budget_alert(self.project)
        refresh_budget_alert(self.project)
        self.assertEqual(Notification.objects.filter(user=self.manager).count(), 1)
        self.project.refresh_from_db()
        self.assertEqual(self.project.budget_alerted_level, 80)
        self.expense('200')
        refresh_budget_alert(self.project)
        self.assertEqual(Notification.objects.filter(user=self.manager).count(), 2)
        Project.objects.filter(pk=self.project.pk).update(budget=Decimal('5000'))
        refresh_budget_alert(self.project)
        self.project.refresh_from_db()
        self.assertEqual(self.project.budget_alerted_level, 0)


class ExpenseWorkflowTests(BudgetBase):
    def post_expense(self, **extra):
        data = {'project': self.project.pk, 'title': 'Laptop', 'amount': '250.50',
                'category': 'hardware', 'incurred_on': self.today.isoformat()}
        data.update(extra)
        return self.client.post(reverse('expense_add'), data)

    def test_member_submits_pending_and_managers_notified(self):
        self.login(self.owner)
        r = self.post_expense()
        self.assertRedirects(r, reverse('expense_list'))
        e = Expense.objects.get()
        self.assertEqual(e.status, 'submitted')
        self.assertTrue(Notification.objects.filter(user=self.manager).exists())

    def test_manager_expense_auto_approved(self):
        self.login(self.manager)
        self.post_expense()
        self.assertEqual(Expense.objects.get().status, 'approved')

    def test_invalid_amount(self):
        self.login(self.owner)
        for bad in ('0', '-5', 'abc'):
            r = self.post_expense(amount=bad)
            self.assertEqual(r.status_code, 200)
        self.assertEqual(Expense.objects.count(), 0)

    def test_task_must_match_project(self):
        other = Project.objects.create(name='Beta')
        task = self.task(project=other)
        self.login(self.owner)
        r = self.post_expense(task=task.pk)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Expense.objects.count(), 0)

    def test_receipt_extension_checked(self):
        self.login(self.owner)
        bad = SimpleUploadedFile('x.exe', b'abc')
        self.assertEqual(self.post_expense(receipt=bad).status_code, 200)
        self.assertEqual(Expense.objects.count(), 0)
        good = SimpleUploadedFile('r.pdf', b'%PDF-1.4')
        self.post_expense(receipt=good)
        self.assertEqual(Expense.objects.count(), 1)

    def test_member_cannot_review(self):
        e = self.expense('10', status='submitted')
        self.login(self.owner)
        self.assertEqual(self.client.post(reverse('expense_approve', args=[e.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('expense_reject', args=[e.pk])).status_code, 403)

    def test_manager_approve_reject(self):
        e = self.expense('10', status='submitted')
        self.login(self.manager)
        self.client.post(reverse('expense_reject', args=[e.pk]), {'note': 'no'})
        e.refresh_from_db()
        self.assertEqual((e.status, e.review_note), ('rejected', 'no'))
        self.client.post(reverse('expense_approve', args=[e.pk]))
        e.refresh_from_db()
        self.assertEqual(e.status, 'approved')

    def test_open_redirect_blocked(self):
        e = self.expense('10', status='submitted')
        self.login(self.manager)
        r = self.client.post(reverse('expense_approve', args=[e.pk]), {'next': 'https://evil.example/'})
        self.assertRedirects(r, reverse('expense_list'))

    def test_visibility_and_delete_rules(self):
        mine = self.expense('10', status='submitted', submitted_by=self.owner)
        theirs = self.expense('20', status='submitted', submitted_by=self.other)
        approved = self.expense('30', submitted_by=self.owner)
        self.login(self.owner)
        r = self.client.get(reverse('expense_list'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context['expenses']), 2)
        self.assertEqual(self.client.post(reverse('expense_delete', args=[theirs.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('expense_delete', args=[approved.pk])).status_code, 403)
        self.client.post(reverse('expense_delete', args=[mine.pk]))
        self.assertFalse(Expense.objects.filter(pk=mine.pk).exists())

    def test_bad_filters_do_not_crash(self):
        self.login(self.manager)
        r = self.client.get(reverse('expense_list') + '?status=zzz&project=abc&category=q&page=-4')
        self.assertEqual(r.status_code, 200)

    def test_project_with_expenses_cannot_be_deleted(self):
        self.expense('10')
        self.login(self.manager)
        self.client.post(reverse('project_delete', args=[self.project.pk]))
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_export_csv_manager_only_and_safe(self):
        self.expense('10', title='=cmd()')
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('expense_export')).status_code, 403)
        self.login(self.manager)
        r = self.client.get(reverse('expense_export'))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode('utf-8')
        self.assertIn("'=cmd()", body)


class BudgetPagesTests(BudgetBase):
    def test_manager_pages_render(self):
        self.expense('900')
        self.expense('5', status='submitted')
        self.login(self.manager)
        for name, args in (('budget_overview', []), ('project_budget', [self.project.pk]),
                           ('expense_list', []), ('expense_add', []), ('project_list', [])):
            r = self.client.get(reverse(name, args=args))
            self.assertEqual(r.status_code, 200, name)
        self.assertContains(self.client.get(reverse('project_list')), 'Budget')

    def test_members_blocked_from_budget(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('budget_overview')).status_code, 403)
        self.assertEqual(self.client.get(reverse('project_budget', args=[self.project.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse('project_list')), 'Budget</a>')

    def test_time_entry_triggers_alert(self):
        set_profile(self.owner, hourly_rate=Decimal('1000'))
        task = self.task(project=self.project)
        self.login(self.owner)
        self.client.post(reverse('add_time_entry', args=[task.pk]),
                         {'hours': 1, 'minutes': 0, 'worked_on': self.today.isoformat(), 'note': ''})
        self.project.refresh_from_db()
        self.assertEqual(self.project.budget_alerted_level, 100)


class WorkloadTests(Base):
    def test_workload_math(self):
        set_profile(self.owner, weekly_capacity_hours=20)
        self.task(title='a', estimated_hours=Decimal('10'), due_date=self.today + datetime.timedelta(days=2))
        self.task(title='b', estimated_hours=Decimal('8'), due_date=self.today - datetime.timedelta(days=1))
        self.task(title='c', due_date=self.today + datetime.timedelta(days=30), estimated_hours=Decimal('50'))
        self.task(title='d')
        self.task(title='done', status='Completed', estimated_hours=Decimal('99'), due_date=self.today)
        row = next(r for r in build_workload(self.today) if r['username'] == 'owner')
        self.assertEqual(row['capacity'], 20)
        self.assertEqual(row['open_tasks'], 4)
        self.assertEqual(row['overdue'], 1)
        self.assertEqual(row['unestimated'], 1)
        self.assertEqual(row['due_soon'], Decimal('18'))
        self.assertEqual(row['utilization'], 90)
        self.assertEqual(row['level'], 'high')

    def test_pages_render(self):
        self.login(self.owner)
        self.task(title='x', due_date=self.today, start_date=self.today - datetime.timedelta(days=3))
        self.assertEqual(self.client.get(reverse('workload')).status_code, 200)
        r = self.client.get(reverse('timeline'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'gantt-bar')
        for q in ('?start=garbage', '?start=0001-01-01', '?project=x&mine=1', '?start=2026-02-30'):
            self.assertEqual(self.client.get(reverse('timeline') + q).status_code, 200, q)

    def test_timeline_positions(self):
        t = self.task(start_date=datetime.date(2026, 1, 12), due_date=datetime.date(2026, 1, 18))
        data = build_timeline([t], datetime.date(2026, 1, 5), today=datetime.date(2026, 1, 6))
        row = data['rows'][0]
        self.assertEqual((row['left'], row['width']), (12.5, 12.5))
        self.assertFalse(row['is_marker'])

    def test_timeline_marker_and_cut(self):
        t = self.task(due_date=datetime.date(2026, 1, 6))
        t2 = self.task(start_date=datetime.date(2025, 12, 1), due_date=datetime.date(2026, 1, 8))
        data = build_timeline([t, t2], datetime.date(2026, 1, 5), today=datetime.date(2026, 1, 6))
        self.assertTrue(data['rows'][0]['is_marker'])
        self.assertTrue(data['rows'][1]['cut_left'])
        self.assertEqual(data['rows'][1]['left'], 0)


class TaskEstimateTests(Base):
    def form_data(self, **kw):
        data = {'title': 'T', 'description': 'd', 'status': 'Pending', 'priority': 'Medium',
                'assigned_to': self.owner.pk, 'progress': 0}
        data.update(kw)
        return data

    def test_start_after_due_rejected(self):
        self.login(self.owner)
        r = self.client.post(reverse('add_task'), self.form_data(
            start_date='2026-03-10', due_date='2026-03-01'))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Task.objects.exists())

    def test_negative_estimate_rejected(self):
        self.login(self.owner)
        r = self.client.post(reverse('add_task'), self.form_data(estimated_hours='-1'))
        self.assertEqual(r.status_code, 200)

    def test_detail_effort_meter(self):
        t = self.task(estimated_hours=Decimal('2'))
        TimeEntry.objects.create(task=t, user=self.owner, minutes=180, worked_on=self.today)
        self.login(self.owner)
        r = self.client.get(reverse('task_detail', args=[t.pk]))
        self.assertTrue(r.context['effort']['over'])
        self.assertContains(r, 'meter-over')

    def test_recurrence_shifts_start_and_copies_estimate(self):
        t = self.task(recurrence='weekly', estimated_hours=Decimal('3'),
                      start_date=datetime.date(2026, 3, 1), due_date=datetime.date(2026, 3, 3))
        t.status = 'Completed'
        t.save()
        nxt = Task.objects.exclude(pk=t.pk).get()
        self.assertEqual(nxt.estimated_hours, Decimal('3'))
        self.assertEqual(nxt.start_date, datetime.date(2026, 3, 8))
        self.assertEqual(nxt.due_date, datetime.date(2026, 3, 10))

    def test_admin_pages(self):
        self.login(self.admin)
        p = Project.objects.create(name='P', budget=5)
        for url in (reverse('admin:core_expense_changelist'), reverse('admin:core_project_changelist'),
                    reverse('admin:auth_user_change', args=[self.owner.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)
