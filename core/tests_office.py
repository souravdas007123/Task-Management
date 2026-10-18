import datetime
import json

from django.contrib.auth.models import User
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from .forms import TaskForm
from .models import LeaveRequest, Notification, Project, Task, UserProfile
from .permissions import can_view_task, visible_projects, visible_tasks
from .services import BlockedError, change_status, find_mentions
from .tests_features import Base


class OfficeBase(Base):
    def setUp(self):
        super().setUp()
        self.outsider = User.objects.create_user('outsider', 'out@example.com', 'pass12345')
        self.secret = Project.objects.create(name='Secret', is_private=True)
        self.secret.members.add(self.owner, self.other)
        self.hidden = self.task(title='Hidden plan', project=self.secret, assigned_to=self.owner, created_by=self.owner)


class VisibilityTests(OfficeBase):
    def test_helpers(self):
        self.assertFalse(can_view_task(self.outsider, self.hidden))
        for user in (self.owner, self.other, self.manager, self.admin):
            self.assertTrue(can_view_task(user, self.hidden), user)
        qs = visible_tasks(Task.objects.all(), self.outsider)
        self.assertFalse(qs.filter(pk=self.hidden.pk).exists())
        self.assertFalse(visible_projects(Project.objects.all(), self.outsider).filter(pk=self.secret.pk).exists())
        self.assertTrue(visible_projects(Project.objects.all(), self.other).filter(pk=self.secret.pk).exists())

    def test_assignee_outside_members_still_sees_own_task(self):
        t = self.task(title='Mine', project=self.secret, assigned_to=self.outsider, created_by=self.owner)
        self.assertTrue(can_view_task(self.outsider, t))
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse('task_detail', args=[t.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('task_detail', args=[self.hidden.pk])).status_code, 404)

    def test_dashboard_board_calendar_hidden(self):
        self.hidden.due_date = self.today
        self.hidden.save()
        self.login(self.outsider)
        for name in ('dashboard', 'board', 'task_calendar', 'timeline', 'task_report'):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)
            self.assertNotContains(r, 'Hidden plan', msg_prefix=name)
        self.assertNotContains(self.client.get(reverse('calendar_ics')), 'Hidden plan')
        self.assertEqual(self.client.get(reverse('dashboard')).context['total_tasks'], 0)
        self.login(self.other)
        self.assertContains(self.client.get(reverse('dashboard')), 'Hidden plan')

    def test_counts_not_duplicated_by_members(self):
        # 2 members + 1 manager-visible task: har task ek hi baar ginna chahiye
        self.login(self.other)
        self.assertEqual(self.client.get(reverse('dashboard')).context['total_tasks'], 1)

    def test_api_hidden(self):
        self.login(self.outsider)
        data = json.loads(self.client.get(reverse('api_task_list')).content)
        self.assertEqual(data['count'], 0)
        self.assertEqual(self.client.get(reverse('api_task_detail', args=[self.hidden.pk])).status_code, 404)
        r = self.client.post(reverse('api_task_status', args=[self.hidden.pk]), {'status': 'Completed'})
        self.assertEqual(r.status_code, 404)
        self.hidden.refresh_from_db()
        self.assertEqual(self.hidden.status, 'Pending')

    def test_sub_actions_hidden(self):
        from .models import ChecklistItem, Comment
        item = ChecklistItem.objects.create(task=self.hidden, text='x')
        comment = Comment.objects.create(task=self.hidden, author=self.owner, body='hi')
        self.login(self.outsider)
        for name, obj in (('toggle_checklist_item', item), ('delete_checklist_item', item), ('delete_comment', comment)):
            self.assertEqual(self.client.post(reverse(name, args=[obj.pk])).status_code, 404, name)
        for name in ('add_comment', 'add_checklist_item', 'add_time_entry', 'add_attachment'):
            self.assertEqual(self.client.post(reverse(name, args=[self.hidden.pk]), {'body': 'x'}).status_code, 404, name)
        for name in ('update_task', 'delete_task'):
            self.assertEqual(self.client.get(reverse(name, args=[self.hidden.pk])).status_code, 404, name)

    def test_project_list_and_filters(self):
        self.login(self.outsider)
        self.assertNotContains(self.client.get(reverse('project_list')), 'Secret')
        r = self.client.get(reverse('dashboard') + f'?project={self.secret.pk}')
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context['current_project'])
        self.login(self.manager)
        self.assertContains(self.client.get(reverse('project_list')), 'Secret')

    def test_forms_limit_choices(self):
        form = TaskForm(user=self.outsider)
        self.assertNotIn(self.secret, form.fields['project'].queryset)
        self.assertNotIn(self.hidden, form.fields['blocked_by'].queryset)
        form = TaskForm(user=self.owner)
        self.assertIn(self.secret, form.fields['project'].queryset)
        # outsider POST se private project choose nahi kar sakta
        self.login(self.outsider)
        data = {'title': 'T', 'description': 'd', 'status': 'Pending', 'priority': 'Medium',
                'assigned_to': self.outsider.pk, 'progress': 0, 'project': self.secret.pk}
        r = self.client.post(reverse('add_task'), data)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Task.objects.filter(title='T').exists())

    def test_expense_form_limits(self):
        self.login(self.outsider)
        r = self.client.get(reverse('expense_add'))
        self.assertNotContains(r, 'Secret')
        r = self.client.get(reverse('expense_add') + f'?task={self.hidden.pk}')
        self.assertEqual(r.context['form'].initial, {})

    def test_private_project_with_tasks_cannot_be_deleted(self):
        self.login(self.manager)
        self.client.post(reverse('project_delete', args=[self.secret.pk]))
        self.assertTrue(Project.objects.filter(pk=self.secret.pk).exists())
        self.hidden.delete()
        self.client.post(reverse('project_delete', args=[self.secret.pk]))
        self.assertFalse(Project.objects.filter(pk=self.secret.pk).exists())

    def test_manager_can_set_members(self):
        self.login(self.manager)
        r = self.client.post(reverse('project_add'), {
            'name': 'Fresh', 'description': '', 'is_private': 'on', 'members': [self.owner.pk],
        })
        self.assertRedirects(r, reverse('project_list'))
        p = Project.objects.get(name='Fresh')
        self.assertTrue(p.is_private)
        self.assertEqual(list(p.members.all()), [self.owner])


class DependencyTests(OfficeBase):
    def setUp(self):
        super().setUp()
        self.a = self.task(title='A first')
        self.b = self.task(title='B second')
        self.b.blocked_by.add(self.a)

    def form_data(self, task, **kw):
        data = {'title': task.title, 'description': 'd', 'status': task.status, 'priority': 'Medium',
                'assigned_to': task.assigned_to_id, 'progress': 0}
        data.update(kw)
        return data

    def test_cannot_complete_while_blocked(self):
        self.login(self.owner)
        r = self.client.post(reverse('update_task', args=[self.b.pk]),
                             self.form_data(self.b, status='Completed', blocked_by=[self.a.pk]))
        self.assertEqual(r.status_code, 200)
        self.b.refresh_from_db()
        self.assertEqual(self.b.status, 'Pending')
        with self.assertRaises(BlockedError):
            change_status(self.b, 'Completed', self.owner)

    def test_api_returns_409(self):
        self.login(self.owner)
        r = self.client.post(reverse('api_task_status', args=[self.b.pk]),
                             json.dumps({'status': 'Completed'}), content_type='application/json')
        self.assertEqual(r.status_code, 409)
        self.assertIn('error', json.loads(r.content))

    def test_allowed_after_blocker_done(self):
        change_status(self.a, 'Completed', self.owner)
        change_status(self.b, 'Completed', self.owner)
        self.b.refresh_from_db()
        self.assertEqual(self.b.status, 'Completed')

    def test_cycle_and_self_block_rejected(self):
        self.login(self.owner)
        r = self.client.post(reverse('update_task', args=[self.a.pk]),
                             self.form_data(self.a, blocked_by=[self.b.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.a.blocked_by.exists())
        form = TaskForm(instance=self.a, user=self.owner)
        self.assertNotIn(self.a, form.fields['blocked_by'].queryset)

    def test_blocker_saved_and_logged(self):
        c = self.task(title='C third')
        self.login(self.owner)
        r = self.client.post(reverse('update_task', args=[c.pk]), self.form_data(c, blocked_by=[self.a.pk]))
        self.assertRedirects(r, reverse('dashboard'))
        self.assertEqual(list(c.blocked_by.all()), [self.a])
        self.assertTrue(c.activity.filter(message__contains='Blocked by').exists())

    def test_detail_shows_both_sides(self):
        self.login(self.owner)
        self.assertContains(self.client.get(reverse('task_detail', args=[self.b.pk])), 'Blocked by')
        self.assertContains(self.client.get(reverse('task_detail', args=[self.a.pk])), 'Blocking')

    def test_completed_blocker_not_listed_but_kept(self):
        change_status(self.a, 'Completed', self.owner)
        form = TaskForm(instance=self.b, user=self.owner)
        self.assertIn(self.a, form.fields['blocked_by'].queryset)  # already linked, so stays selectable


class MentionTests(OfficeBase):
    def test_find_mentions(self):
        t = self.task()
        text = 'hey @other, and @OWNER. also @nobody and me@example.com'
        names = sorted(u.username for u in find_mentions(text, t))
        self.assertEqual(names, ['other', 'owner'])

    def test_inactive_and_hidden_users_skipped(self):
        self.other.is_active = False
        self.other.save()
        self.assertEqual(find_mentions('@other @outsider', self.hidden), [])

    def test_comment_notifies_mentioned_once(self):
        t = self.task(assigned_to=self.owner, created_by=self.owner)
        self.login(self.other)
        self.client.post(reverse('add_comment', args=[t.pk]), {'body': 'please look @owner'})
        msgs = list(Notification.objects.filter(user=self.owner).values_list('message', flat=True))
        self.assertEqual(len(msgs), 1)
        self.assertIn('mentioned you', msgs[0])

    def test_unrelated_mention_and_normal_comment(self):
        t = self.task(assigned_to=self.owner, created_by=self.owner)
        self.login(self.other)
        self.client.post(reverse('add_comment', args=[t.pk]), {'body': 'cc @boss'})
        self.assertTrue(Notification.objects.filter(user=self.manager, message__contains='mentioned').exists())
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='commented').exists())


class LeaveTests(OfficeBase):
    def apply(self, user, start=None, end=None, **kw):
        start = start or self.today
        end = end or self.today + datetime.timedelta(days=2)
        self.login(user)
        data = {'kind': 'annual', 'start_date': start.isoformat(), 'end_date': end.isoformat(), 'reason': 'trip'}
        data.update(kw)
        return self.client.post(reverse('leave_add'), data)

    def test_submit_notifies_managers(self):
        r = self.apply(self.owner)
        self.assertRedirects(r, reverse('leave_list'))
        leave = LeaveRequest.objects.get()
        self.assertEqual((leave.status, leave.days), ('pending', 3))
        self.assertTrue(Notification.objects.filter(user=self.manager, message__contains='asked for leave').exists())

    def test_invalid_dates(self):
        r = self.apply(self.owner, start=self.today, end=self.today - datetime.timedelta(days=1))
        self.assertEqual(r.status_code, 200)
        r = self.apply(self.owner, end=self.today + datetime.timedelta(days=400))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_review_permissions(self):
        self.apply(self.owner)
        leave = LeaveRequest.objects.get()
        self.login(self.other)
        self.assertEqual(self.client.post(reverse('leave_approve', args=[leave.pk])).status_code, 403)
        self.login(self.manager)
        self.client.post(reverse('leave_reject', args=[leave.pk]), {'note': 'busy week'})
        leave.refresh_from_db()
        self.assertEqual((leave.status, leave.review_note), ('rejected', 'busy week'))
        self.client.post(reverse('leave_approve', args=[leave.pk]))
        leave.refresh_from_db()
        self.assertEqual(leave.status, 'approved')
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='approved').exists())

    def test_cannot_approve_own_unless_superuser(self):
        self.apply(self.manager)
        leave = LeaveRequest.objects.get()
        self.client.post(reverse('leave_approve', args=[leave.pk]))
        leave.refresh_from_db()
        self.assertEqual(leave.status, 'pending')
        self.login(self.admin)
        self.client.post(reverse('leave_approve', args=[leave.pk]))
        leave.refresh_from_db()
        self.assertEqual(leave.status, 'approved')

    def test_cancel_rules(self):
        self.apply(self.owner)
        leave = LeaveRequest.objects.get()
        self.login(self.other)
        self.assertEqual(self.client.post(reverse('leave_cancel', args=[leave.pk])).status_code, 404)
        self.login(self.owner)
        self.client.post(reverse('leave_cancel', args=[leave.pk]))
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.apply(self.owner)
        LeaveRequest.objects.update(status='approved')
        self.client.post(reverse('leave_cancel', args=[LeaveRequest.objects.get().pk]))
        self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_list_scope_and_filters(self):
        self.apply(self.owner)
        self.apply(self.other)
        self.login(self.owner)
        self.assertEqual(len(self.client.get(reverse('leave_list')).context['leaves']), 1)
        self.login(self.manager)
        self.assertEqual(len(self.client.get(reverse('leave_list')).context['leaves']), 2)
        r = self.client.get(reverse('leave_list') + '?status=zzz&page=-2')
        self.assertEqual(r.status_code, 200)

    def test_workload_marks_away(self):
        self.apply(self.owner)
        LeaveRequest.objects.update(status='approved')
        self.login(self.manager)
        r = self.client.get(reverse('workload'))
        away = {row['username']: row['away'] for row in r.context['rows']}
        self.assertTrue(away['owner'])
        self.assertFalse(away['other'])
        self.assertContains(r, 'On leave')


class AuditTests(OfficeBase):
    def test_manager_only(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('audit_log')).status_code, 403)
        self.login(self.manager)
        r = self.client.get(reverse('audit_log'))
        self.assertEqual(r.status_code, 200)

    def test_filters(self):
        from .services import log_activity
        t = self.task()
        log_activity(t, self.owner, 'Changed status from "A" to "B"')
        log_activity(t, self.other, 'Edited the description')
        self.login(self.manager)
        r = self.client.get(reverse('audit_log') + f'?actor={self.owner.pk}')
        self.assertContains(r, 'Changed status')
        self.assertNotContains(r, 'Edited the description')
        r = self.client.get(reverse('audit_log') + '?q=description')
        self.assertContains(r, 'Edited the description')
        for q in ('?from=bad&to=2026-99-99', '?actor=abc&page=zz', '?from=0001-01-01'):
            self.assertEqual(self.client.get(reverse('audit_log') + q).status_code, 200, q)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordTests(OfficeBase):
    def test_reset_flow(self):
        self.assertEqual(self.client.get(reverse('password_reset')).status_code, 200)
        r = self.client.post(reverse('password_reset'), {'email': 'owner@example.com'})
        self.assertRedirects(r, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn('Office Hub account', body)
        self.assertEqual(mail.outbox[0].subject, 'Office Hub password reset')
        link = [line for line in body.splitlines() if '/password/reset/' in line][0].strip()
        path = link.split('://', 1)[1].split('/', 1)[1]
        r = self.client.get('/' + path, follow=True)
        self.assertEqual(r.status_code, 200)
        r = self.client.post(r.request['PATH_INFO'], {'new_password1': 'Brand-new-pass-77', 'new_password2': 'Brand-new-pass-77'})
        self.assertRedirects(r, reverse('password_reset_complete'))
        self.assertTrue(User.objects.get(username='owner').check_password('Brand-new-pass-77'))

    def test_unknown_email_gives_same_response(self):
        r = self.client.post(reverse('password_reset'), {'email': 'nobody@example.com'})
        self.assertRedirects(r, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_bad_token(self):
        r = self.client.get(reverse('password_reset_confirm', args=['abc', 'def-ghi']))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'expired')

    def test_change_password(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('password_change')).status_code, 200)
        r = self.client.post(reverse('password_change'), {
            'old_password': 'pass12345', 'new_password1': 'Another-strong-88', 'new_password2': 'Another-strong-88',
        })
        self.assertRedirects(r, reverse('password_change_done'))
        self.assertEqual(self.client.get(reverse('password_change')).status_code, 200)  # session zinda hai

    def test_change_requires_login_and_login_page_links_reset(self):
        self.assertEqual(self.client.get(reverse('password_change')).status_code, 302)
        self.assertContains(self.client.get(reverse('login')), reverse('password_reset'))
