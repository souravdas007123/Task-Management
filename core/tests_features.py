import datetime
import json
import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import (
    ActivityLog, Attachment, ChecklistItem, Comment, Label, Notification, Project,
    Task, TimeEntry, UserProfile, add_months,
)
from .permissions import can_delete_task, can_edit_task, role_of

TEST_MEDIA = tempfile.mkdtemp(prefix='taskboard-test-media-')


def tearDownModule():
    shutil.rmtree(TEST_MEDIA, ignore_errors=True)


@override_settings(
    MEDIA_ROOT=TEST_MEDIA,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],  # tests tez chalein
)
class Base(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', 'owner@example.com', 'pass12345')
        self.other = User.objects.create_user('other', 'other@example.com', 'pass12345')
        self.manager = User.objects.create_user('boss', 'boss@example.com', 'pass12345')
        UserProfile.objects.create(user=self.manager, role='manager')
        self.admin = User.objects.create_superuser('root', 'root@example.com', 'pass12345')
        self.today = timezone.localdate()

    def task(self, **kwargs):
        defaults = dict(title='Sample', description='desc', assigned_to=self.owner, created_by=self.owner)
        defaults.update(kwargs)
        labels = defaults.pop('labels', None)
        task = Task.objects.create(**defaults)
        if labels:
            task.labels.set(labels)
        return task

    def login(self, user):
        self.client.force_login(user)


# ---------------------------------------------------------------- models

class ModelBehaviourTests(Base):
    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(add_months(datetime.date(2026, 1, 31)), datetime.date(2026, 2, 28))
        self.assertEqual(add_months(datetime.date(2028, 1, 31)), datetime.date(2028, 2, 29))
        self.assertEqual(add_months(datetime.date(2026, 12, 15)), datetime.date(2027, 1, 15))

    def test_completed_at_follows_status(self):
        done = self.task(status='Completed')
        self.assertIsNotNone(done.completed_at)
        done.status = 'Pending'
        done.save()
        done.refresh_from_db()
        self.assertIsNone(done.completed_at)

    def test_completed_at_set_when_saving_with_update_fields(self):
        task = self.task()
        task.status = 'Completed'
        task.save(update_fields=['status'])
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)

    def test_is_overdue(self):
        yesterday = self.today - datetime.timedelta(days=1)
        self.assertTrue(self.task(due_date=yesterday).is_overdue)
        self.assertFalse(self.task(due_date=yesterday, status='Completed').is_overdue)
        self.assertFalse(self.task(due_date=self.today).is_overdue)
        self.assertFalse(self.task().is_overdue)

    def test_weekly_recurrence_creates_next_task_once(self):
        label = Label.objects.create(name='Bug')
        task = self.task(recurrence='weekly', due_date=datetime.date(2026, 1, 5), labels=[label])
        ChecklistItem.objects.create(task=task, text='Step', is_done=True)

        task.status = 'Completed'
        task.save()

        self.assertEqual(Task.objects.count(), 2)
        nxt = Task.objects.exclude(pk=task.pk).get()
        self.assertEqual(nxt.due_date, datetime.date(2026, 1, 12))
        self.assertEqual((nxt.status, nxt.progress, nxt.recurrence), ('Pending', 0, 'weekly'))
        self.assertEqual(list(nxt.labels.all()), [label])
        self.assertEqual([(i.text, i.is_done) for i in nxt.checklist.all()], [('Step', False)])
        self.assertTrue(ActivityLog.objects.filter(task=nxt).exists())
        task.refresh_from_db()
        self.assertTrue(task.recurrence_spawned)

        # dobara complete karne par duplicate nahi banta
        task.status = 'Pending'
        task.save()
        task.status = 'Completed'
        task.save()
        self.assertEqual(Task.objects.count(), 2)

    def test_monthly_and_daily_recurrence(self):
        monthly = self.task(recurrence='monthly', due_date=datetime.date(2026, 1, 31))
        monthly.status = 'Completed'
        monthly.save()
        self.assertEqual(Task.objects.exclude(pk=monthly.pk).get().due_date, datetime.date(2026, 2, 28))

        daily = self.task(title='Daily', recurrence='daily', due_date=datetime.date(2026, 3, 1))
        daily.status = 'Completed'
        daily.save()
        self.assertEqual(Task.objects.get(title='Daily', status='Pending').due_date, datetime.date(2026, 3, 2))

    def test_no_recurrence_when_not_repeating_or_created_completed(self):
        plain = self.task()
        plain.status = 'Completed'
        plain.save()
        self.task(recurrence='daily', status='Completed')
        self.assertEqual(Task.objects.count(), 2)

    def test_progress_syncs_from_checklist(self):
        task = self.task()
        ChecklistItem.objects.create(task=task, text='a', is_done=True)
        ChecklistItem.objects.create(task=task, text='b')
        ChecklistItem.objects.create(task=task, text='c')
        ChecklistItem.objects.create(task=task, text='d')
        task.sync_progress_from_checklist()
        task.refresh_from_db()
        self.assertEqual(task.progress, 25)

    def test_deleting_project_keeps_tasks(self):
        project = Project.objects.create(name='P')
        task = self.task(project=project)
        project.delete()
        task.refresh_from_db()
        self.assertIsNone(task.project)


# ---------------------------------------------------------------- permissions

class PermissionTests(Base):
    def test_roles(self):
        self.assertEqual(role_of(self.admin), 'admin')
        self.assertEqual(role_of(self.manager), 'manager')
        self.assertEqual(role_of(self.owner), 'member')

    def test_edit_and_delete_rules(self):
        task = self.task(created_by=self.owner, assigned_to=self.owner)
        self.assertTrue(can_edit_task(self.owner, task))
        self.assertTrue(can_delete_task(self.owner, task))
        self.assertFalse(can_edit_task(self.other, task))
        self.assertFalse(can_delete_task(self.other, task))
        self.assertTrue(can_edit_task(self.manager, task))
        self.assertTrue(can_delete_task(self.admin, task))

        assigned_only = self.task(created_by=self.manager, assigned_to=self.other)
        self.assertTrue(can_edit_task(self.other, assigned_only))
        self.assertFalse(can_delete_task(self.other, assigned_only))

    def test_member_cannot_edit_or_delete_others_task(self):
        task = self.task()
        self.login(self.other)
        self.assertEqual(self.client.get(reverse('update_task', args=[task.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('update_task', args=[task.pk]), {'title': 'x'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('delete_task', args=[task.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('delete_task', args=[task.pk])).status_code, 403)
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

    def test_member_cannot_modify_details_of_others_task(self):
        task = self.task()
        item = ChecklistItem.objects.create(task=task, text='x')
        self.login(self.other)
        self.assertEqual(self.client.post(reverse('add_checklist_item', args=[task.pk]), {'text': 'y'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('toggle_checklist_item', args=[item.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('delete_checklist_item', args=[item.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('add_time_entry', args=[task.pk]), {'hours': 1, 'minutes': 0, 'worked_on': self.today}).status_code, 403)
        f = SimpleUploadedFile('a.pdf', b'%PDF-1.4')
        self.assertEqual(self.client.post(reverse('add_attachment', args=[task.pk]), {'file': f}).status_code, 403)

    def test_anyone_logged_in_can_comment(self):
        task = self.task()
        self.login(self.other)
        self.client.post(reverse('add_comment', args=[task.pk]), {'body': 'Looks good'})
        self.assertEqual(task.comments.count(), 1)

    def test_manager_can_edit_and_delete_any_task(self):
        task = self.task()
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse('update_task', args=[task.pk])).status_code, 200)
        self.client.post(reverse('delete_task', args=[task.pk]))
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

    def test_manager_only_pages_forbid_members(self):
        project = Project.objects.create(name='P')
        label = Label.objects.create(name='L')
        self.login(self.owner)
        for name, args in [('project_add', []), ('project_edit', [project.pk]), ('project_delete', [project.pk])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)
        self.assertEqual(self.client.post(reverse('label_add'), {'name': 'x', 'color': '#ffffff'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('label_delete', args=[label.pk])).status_code, 403)
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())

    def test_403_page_is_themed(self):
        task = self.task()
        self.login(self.other)
        response = self.client.get(reverse('update_task', args=[task.pk]))
        self.assertContains(response, 'Access denied', status_code=403)

    def test_404_page_is_themed(self):
        self.login(self.owner)
        response = self.client.get(reverse('task_detail', args=[99999]))
        self.assertContains(response, 'Page not found', status_code=404)

    def test_anonymous_redirected_everywhere(self):
        task = self.task()
        urls = [
            reverse('board'), reverse('task_calendar'), reverse('calendar_ics'), reverse('project_list'),
            reverse('label_list'), reverse('analytics'), reverse('notifications'),
            reverse('task_detail', args=[task.pk]), reverse('delete_task', args=[task.pk]),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse('login'), response['Location'], url)

    def test_post_only_actions_reject_get(self):
        task = self.task()
        self.login(self.owner)
        for name in ('add_comment', 'add_checklist_item', 'add_attachment', 'add_time_entry'):
            self.assertEqual(self.client.get(reverse(name, args=[task.pk])).status_code, 405, name)


# ---------------------------------------------------------------- dashboard filters

class DashboardFilterTests(Base):
    def setUp(self):
        super().setUp()
        self.login(self.owner)
        self.bug = Label.objects.create(name='Bug')
        self.project = Project.objects.create(name='Alpha')
        yesterday = self.today - datetime.timedelta(days=1)
        self.t1 = self.task(title='Fix login bug', status='Pending', priority='High', due_date=yesterday, labels=[self.bug], project=self.project)
        self.t2 = self.task(title='Write docs', status='Completed', priority='Low', assigned_to=self.other, due_date=yesterday)
        self.t3 = self.task(title='Design logo', status='In Progress', priority='Medium', due_date=self.today + datetime.timedelta(days=3))

    def titles(self, **params):
        response = self.client.get(reverse('dashboard'), params)
        self.assertEqual(response.status_code, 200)
        return [t.title for t in response.context['tasks']]

    def test_search(self):
        self.assertEqual(self.titles(q='login'), ['Fix login bug'])
        self.assertEqual(self.titles(q='OTHER'), ['Write docs'])  # assignee username, case-insensitive
        self.assertEqual(self.titles(q='nothing-matches'), [])

    def test_filters(self):
        self.assertEqual(self.titles(status='Completed'), ['Write docs'])
        self.assertEqual(self.titles(priority='High'), ['Fix login bug'])
        self.assertEqual(self.titles(assignee=self.other.pk), ['Write docs'])
        self.assertEqual(self.titles(label=self.bug.pk), ['Fix login bug'])
        self.assertEqual(self.titles(project=self.project.pk), ['Fix login bug'])
        self.assertEqual(self.titles(overdue='1'), ['Fix login bug'])  # completed overdue nahi ginta

    def test_mine(self):
        self.assertEqual(sorted(self.titles(mine='1')), ['Design logo', 'Fix login bug'])

    def test_sorting(self):
        self.assertEqual(self.titles(sort='priority'), ['Fix login bug', 'Design logo', 'Write docs'])
        self.assertEqual(self.titles(sort='title'), ['Design logo', 'Fix login bug', 'Write docs'])
        due = self.titles(sort='due')
        self.assertEqual(due[-1], 'Design logo')  # sabse door ki due date

    def test_due_sort_puts_tasks_without_due_date_last(self):
        self.task(title='No date')
        self.assertEqual(self.titles(sort='due')[-1], 'No date')

    def test_bad_parameters_do_not_crash(self):
        for params in [
            {'assignee': 'abc'}, {'label': '-1'}, {'project': '99999999999999999999'},
            {'page': 'abc'}, {'page': '999'}, {'sort': 'bogus'}, {'status': 'nope'}, {'priority': 'nope'},
            {'overdue': 'maybe'}, {'q': 'x' * 500},
        ]:
            self.assertEqual(self.client.get(reverse('dashboard'), params).status_code, 200, params)

    def test_pagination(self):
        for i in range(12):
            self.task(title=f'Bulk {i}')
        first = self.client.get(reverse('dashboard'))
        self.assertEqual(len(first.context['tasks']), 10)
        self.assertEqual(first.context['page_obj'].paginator.num_pages, 2)
        second = self.client.get(reverse('dashboard'), {'page': 2})
        self.assertEqual(len(second.context['tasks']), 5)
        self.assertContains(first, 'Page 1 of 2')

    def test_pagination_links_keep_filters(self):
        for i in range(12):
            self.task(title=f'Bulk {i}', priority='High')
        response = self.client.get(reverse('dashboard'), {'priority': 'High'})
        self.assertContains(response, 'priority=High&amp;page=2')

    def test_stats_follow_scope(self):
        response = self.client.get(reverse('dashboard'), {'mine': '1'})
        self.assertEqual(response.context['total_tasks'], 2)
        self.assertEqual(response.context['overdue_count'], 1)

    def test_query_count_does_not_grow_with_tasks(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('dashboard'))
            return len(ctx)
        before = count()
        for i in range(8):
            self.task(title=f'N+1 {i}', labels=[self.bug], project=self.project)
        self.assertEqual(count(), before)


# ---------------------------------------------------------------- task create / update / delete

class TaskCrudTests(Base):
    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def payload(self, **kwargs):
        data = {'title': 'New', 'description': 'd', 'assigned_to': self.other.pk, 'status': 'Pending',
                'priority': 'Medium', 'progress': 0, 'recurrence': 'none'}
        data.update(kwargs)
        return data

    def test_add_with_project_labels_and_recurrence(self):
        project = Project.objects.create(name='P')
        label = Label.objects.create(name='L')
        self.client.post(reverse('add_task'), self.payload(project=project.pk, labels=[label.pk], recurrence='weekly'))
        task = Task.objects.get(title='New')
        self.assertEqual((task.project, task.recurrence, task.created_by), (project, 'weekly', self.owner))
        self.assertEqual(list(task.labels.all()), [label])
        self.assertTrue(task.activity.filter(message='Created the task').exists())

    def test_add_notifies_assignee_by_notification_and_email(self):
        self.client.post(reverse('add_task'), self.payload())
        note = Notification.objects.get(user=self.other)
        self.assertIn('assigned you "New"', note.message)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['other@example.com'])

    def test_assigning_to_yourself_sends_nothing(self):
        self.client.post(reverse('add_task'), self.payload(assigned_to=self.owner.pk))
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_add_task_prefills_project_from_query(self):
        project = Project.objects.create(name='P')
        response = self.client.get(reverse('add_task'), {'project': project.pk})
        self.assertEqual(response.context['form'].initial['project'], project.pk)

    def test_update_logs_each_change_and_notifies(self):
        task = self.task(assigned_to=self.owner, created_by=self.other)
        self.client.post(reverse('update_task', args=[task.pk]), self.payload(
            title='Renamed', assigned_to=self.owner.pk, status='Completed', progress=100, priority='High',
        ))
        messages = list(task.activity.values_list('message', flat=True))
        self.assertIn('Changed title from "Sample" to "Renamed"', messages)
        self.assertIn('Changed status from "Pending" to "Completed"', messages)
        self.assertIn('Changed priority from "Medium" to "High"', messages)
        self.assertIn('Changed progress from "0%" to "100%"', messages)
        # creator (other) ko status change ki notification
        self.assertTrue(Notification.objects.filter(user=self.other, message__contains='changed "Renamed" to Completed').exists())

    def test_update_reassign_notifies_new_assignee(self):
        task = self.task()
        self.client.post(reverse('update_task', args=[task.pk]), self.payload(assigned_to=self.other.pk))
        self.assertTrue(Notification.objects.filter(user=self.other, message__contains='assigned you').exists())

    def test_update_without_changes_logs_nothing(self):
        task = self.task(title='Same', description='d', assigned_to=self.other)
        before = task.activity.count()
        self.client.post(reverse('update_task', args=[task.pk]), self.payload(title='Same', description='d'))
        self.assertEqual(task.activity.count(), before)

    def test_update_form_without_recurrence_keeps_value(self):
        task = self.task(recurrence='weekly')
        data = self.payload()
        data.pop('recurrence')
        self.client.post(reverse('update_task', args=[task.pk]), data)
        task.refresh_from_db()
        self.assertEqual(task.recurrence, 'weekly')

    def test_completing_through_form_spawns_next_repeat(self):
        task = self.task(recurrence='daily', due_date=datetime.date(2026, 5, 1), assigned_to=self.owner)
        self.client.post(reverse('update_task', args=[task.pk]), self.payload(
            assigned_to=self.owner.pk, status='Completed', recurrence='daily', due_date='2026-05-01', progress=100,
        ))
        self.assertTrue(Task.objects.filter(due_date=datetime.date(2026, 5, 2), status='Pending').exists())

    def test_inactive_user_cannot_be_assigned(self):
        ghost = User.objects.create_user('ghost', password='x', is_active=False)
        response = self.client.post(reverse('add_task'), self.payload(assigned_to=ghost.pk))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.filter(title='New').exists())

    def test_delete_confirm_then_delete_removes_files(self):
        task = self.task()
        attachment = Attachment.objects.create(task=task, file=SimpleUploadedFile('a.txt', b'hello'), original_name='a.txt')
        path = attachment.file.path
        self.assertTrue(os.path.exists(path))

        confirm = self.client.get(reverse('delete_task', args=[task.pk]))
        self.assertContains(confirm, 'Delete')
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())  # GET se delete nahi hota

        self.client.post(reverse('delete_task', args=[task.pk]))
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())
        self.assertFalse(os.path.exists(path))


# ---------------------------------------------------------------- detail page actions

class TaskDetailTests(Base):
    def setUp(self):
        super().setUp()
        self.login(self.owner)
        self.t = self.task(assigned_to=self.owner, created_by=self.other)

    def test_detail_renders_everything(self):
        ChecklistItem.objects.create(task=self.t, text='Step one')
        Comment.objects.create(task=self.t, author=self.other, body='Hello <b>team</b>')
        TimeEntry.objects.create(task=self.t, user=self.owner, minutes=90)
        response = self.client.get(self.t.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Step one')
        self.assertContains(response, '1h 30m')
        self.assertContains(response, 'Hello &lt;b&gt;team&lt;/b&gt;')  # HTML escape hota hai

    def test_comment_notifies_but_not_the_author(self):
        self.client.post(reverse('add_comment', args=[self.t.pk]), {'body': 'Please review'})
        self.assertEqual(self.t.comments.get().author, self.owner)
        self.assertTrue(Notification.objects.filter(user=self.other, message__contains='commented').exists())
        self.assertFalse(Notification.objects.filter(user=self.owner).exists())

    def test_blank_comment_rejected(self):
        response = self.client.post(reverse('add_comment', args=[self.t.pk]), {'body': '   '}, follow=True)
        self.assertEqual(self.t.comments.count(), 0)
        self.assertContains(response, 'Write something before posting.')

    def test_only_author_or_manager_can_delete_comment(self):
        comment = Comment.objects.create(task=self.t, author=self.other, body='x')
        self.assertEqual(self.client.post(reverse('delete_comment', args=[comment.pk])).status_code, 403)
        self.login(self.other)
        self.client.post(reverse('delete_comment', args=[comment.pk]))
        self.assertEqual(Comment.objects.count(), 0)

    def test_checklist_flow_updates_progress(self):
        self.client.post(reverse('add_checklist_item', args=[self.t.pk]), {'text': 'One'})
        self.client.post(reverse('add_checklist_item', args=[self.t.pk]), {'text': 'Two'})
        first, second = self.t.checklist.all()
        self.assertEqual((first.position, second.position), (1, 2))

        self.client.post(reverse('toggle_checklist_item', args=[first.pk]))
        self.t.refresh_from_db()
        self.assertEqual(self.t.progress, 50)

        self.client.post(reverse('toggle_checklist_item', args=[second.pk]))
        self.t.refresh_from_db()
        self.assertEqual(self.t.progress, 100)

        self.client.post(reverse('toggle_checklist_item', args=[second.pk]))  # wapas undone
        self.client.post(reverse('delete_checklist_item', args=[second.pk]))
        self.t.refresh_from_db()
        self.assertEqual(self.t.progress, 100)  # bacha hua ek item done hai

    def test_blank_checklist_item_rejected(self):
        self.client.post(reverse('add_checklist_item', args=[self.t.pk]), {'text': '  '})
        self.assertEqual(self.t.checklist.count(), 0)

    def test_attachment_upload_and_remove(self):
        upload = SimpleUploadedFile('spec.pdf', b'%PDF-1.4 content', content_type='application/pdf')
        self.client.post(reverse('add_attachment', args=[self.t.pk]), {'file': upload})
        attachment = self.t.attachments.get()
        self.assertEqual((attachment.original_name, attachment.uploaded_by), ('spec.pdf', self.owner))
        path = attachment.file.path
        self.assertTrue(os.path.exists(path))

        self.client.post(reverse('delete_attachment', args=[attachment.pk]))
        self.assertEqual(self.t.attachments.count(), 0)
        self.assertFalse(os.path.exists(path))

    def test_dangerous_file_types_rejected(self):
        for name in ('virus.exe', 'page.html', 'shell.php', 'script.js', 'noextension'):
            self.client.post(reverse('add_attachment', args=[self.t.pk]), {'file': SimpleUploadedFile(name, b'x')})
        self.assertEqual(self.t.attachments.count(), 0)

    def test_oversized_file_rejected(self):
        big = SimpleUploadedFile('big.pdf', b'x' * (10 * 1024 * 1024 + 1))
        self.client.post(reverse('add_attachment', args=[self.t.pk]), {'file': big})
        self.assertEqual(self.t.attachments.count(), 0)

    def test_time_entries(self):
        self.client.post(reverse('add_time_entry', args=[self.t.pk]), {'hours': 1, 'minutes': 15, 'note': 'Docs', 'worked_on': self.today})
        entry = self.t.time_entries.get()
        self.assertEqual((entry.minutes, entry.duration_display, entry.user), (75, '1h 15m', self.owner))
        response = self.client.get(self.t.get_absolute_url())
        self.assertEqual(response.context['total_time'], '1h 15m')

        self.client.post(reverse('delete_time_entry', args=[entry.pk]))
        self.assertEqual(self.t.time_entries.count(), 0)

    def test_time_entry_validation(self):
        for data in ({'hours': 0, 'minutes': 0}, {'hours': 25, 'minutes': 0}, {'hours': 0, 'minutes': 75}, {'hours': 'x', 'minutes': 1}):
            data['worked_on'] = self.today
            self.client.post(reverse('add_time_entry', args=[self.t.pk]), data)
        self.assertEqual(self.t.time_entries.count(), 0)

    def test_others_time_entry_cannot_be_deleted_by_member(self):
        entry = TimeEntry.objects.create(task=self.t, user=self.other, minutes=10)
        self.assertEqual(self.client.post(reverse('delete_time_entry', args=[entry.pk])).status_code, 403)

    def test_history_shows_actions(self):
        self.client.post(reverse('add_checklist_item', args=[self.t.pk]), {'text': 'One'})
        response = self.client.get(self.t.get_absolute_url())
        self.assertContains(response, 'Added checklist item')


# ---------------------------------------------------------------- board, calendar, notifications

class BoardCalendarTests(Base):
    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def test_board_groups_by_status(self):
        self.task(title='A', status='Pending')
        self.task(title='B', status='Completed')
        response = self.client.get(reverse('board'))
        self.assertEqual(response.status_code, 200)
        columns = {c['status']: [t.title for t in c['tasks']] for c in response.context['columns']}
        self.assertEqual(columns['Pending'], ['A'])
        self.assertEqual(columns['Completed'], ['B'])
        self.assertEqual(columns['On Hold'], [])

    def test_board_only_marks_editable_cards_draggable(self):
        self.task(title='Mine')
        self.task(title='Theirs', assigned_to=self.other, created_by=self.other)
        response = self.client.get(reverse('board'))
        self.assertContains(response, ' draggable="true">', count=1)  # sirf card markup, JS selector nahi

    def test_board_filters(self):
        project = Project.objects.create(name='P')
        self.task(title='In P', project=project)
        self.task(title='Elsewhere')
        response = self.client.get(reverse('board'), {'project': project.pk})
        titles = [t.title for c in response.context['columns'] for t in c['tasks']]
        self.assertEqual(titles, ['In P'])
        self.assertEqual(self.client.get(reverse('board'), {'project': 'zzz', 'mine': '1'}).status_code, 200)

    def test_calendar_places_tasks_on_due_date(self):
        self.task(title='Report', due_date=datetime.date(2026, 2, 14))
        response = self.client.get(reverse('task_calendar'), {'year': 2026, 'month': 2})
        self.assertContains(response, 'February 2026')
        placed = [(cell['date'], [t.title for t in cell['tasks']]) for week in response.context['weeks'] for cell in week if cell['tasks']]
        self.assertEqual(placed, [(datetime.date(2026, 2, 14), ['Report'])])

    def test_calendar_more_than_three_tasks(self):
        for i in range(5):
            self.task(title=f'T{i}', due_date=datetime.date(2026, 2, 10))
        response = self.client.get(reverse('task_calendar'), {'year': 2026, 'month': 2})
        cell = [c for w in response.context['weeks'] for c in w if c['date'] == datetime.date(2026, 2, 10)][0]
        self.assertEqual((len(cell['tasks']), cell['extra']), (3, 2))

    def test_calendar_navigation_and_bad_input(self):
        december = self.client.get(reverse('task_calendar'), {'year': 2026, 'month': 12})
        self.assertEqual(december.context['next'], {'year': 2027, 'month': 1})
        january = self.client.get(reverse('task_calendar'), {'year': 2026, 'month': 1})
        self.assertEqual(january.context['prev'], {'year': 2025, 'month': 12})
        for params in ({'year': 'x', 'month': 'y'}, {'year': 99999, 'month': 1}, {'year': 2026, 'month': 13}, {'year': 0, 'month': 0}):
            self.assertEqual(self.client.get(reverse('task_calendar'), params).status_code, 200, params)

    def test_ics_export(self):
        self.task(title='Ship, release; v2', due_date=datetime.date(2026, 3, 9), description='x')
        self.task(title='Done already', due_date=datetime.date(2026, 3, 9), status='Completed')
        self.task(title='No date')
        response = self.client.get(reverse('calendar_ics'))
        self.assertEqual(response['Content-Type'], 'text/calendar; charset=utf-8')
        body = response.content.decode()
        self.assertTrue(body.startswith('BEGIN:VCALENDAR\r\n'))
        self.assertTrue(body.endswith('END:VCALENDAR\r\n'))
        self.assertIn('DTSTART;VALUE=DATE:20260309', body)
        self.assertIn('DTEND;VALUE=DATE:20260310', body)
        self.assertIn('SUMMARY:Ship\\, release\\; v2', body)
        self.assertNotIn('Done already', body)
        self.assertNotIn('No date', body)
        self.assertEqual(body.count('BEGIN:VEVENT'), 1)

    def test_ics_long_lines_are_folded(self):
        self.task(title='L' * 200, due_date=datetime.date(2026, 3, 9))
        body = self.client.get(reverse('calendar_ics')).content.decode()
        for line in body.split('\r\n'):
            self.assertLessEqual(len(line.encode()), 75, line)


class NotificationTests(Base):
    def setUp(self):
        super().setUp()
        self.login(self.owner)
        self.task_obj = self.task()
        self.n1 = Notification.objects.create(user=self.owner, message='One', link=self.task_obj.get_absolute_url())
        self.n2 = Notification.objects.create(user=self.owner, message='Two')

    def test_list_and_badge(self):
        response = self.client.get(reverse('notifications'))
        self.assertContains(response, 'One')
        self.assertEqual(response.context['unread_notifications_count'], 2)

    def test_open_marks_read_and_redirects(self):
        response = self.client.get(reverse('notification_open', args=[self.n1.pk]))
        self.assertRedirects(response, self.task_obj.get_absolute_url())
        self.n1.refresh_from_db()
        self.assertTrue(self.n1.is_read)

    def test_open_without_link_goes_to_list(self):
        response = self.client.get(reverse('notification_open', args=[self.n2.pk]))
        self.assertRedirects(response, reverse('notifications'))

    def test_open_ignores_external_links(self):
        self.n2.link = 'https://evil.example/phish'
        self.n2.save()
        response = self.client.get(reverse('notification_open', args=[self.n2.pk]))
        self.assertRedirects(response, reverse('notifications'))

    def test_cannot_open_someone_elses_notification(self):
        theirs = Notification.objects.create(user=self.other, message='Private')
        self.assertEqual(self.client.get(reverse('notification_open', args=[theirs.pk])).status_code, 404)

    def test_mark_all_read(self):
        self.client.post(reverse('notifications_mark_read'))
        self.assertEqual(Notification.objects.filter(user=self.owner, is_read=False).count(), 0)

    def test_notify_skips_actor_and_inactive_and_survives_email_failure(self):
        from .services import notify
        self.assertIsNone(notify(self.owner, 'self', actor=self.owner))
        self.assertIsNone(notify(User.objects.create_user('off', is_active=False), 'x'))
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='127.0.0.1', EMAIL_PORT=1):
            self.assertIsNotNone(notify(self.other, 'hello'))  # SMTP fail ho to bhi error nahi

    def test_reminder_command_is_idempotent(self):
        yesterday = self.today - datetime.timedelta(days=1)
        self.task(title='Late', due_date=yesterday)
        self.task(title='Today', due_date=self.today)
        self.task(title='Later', due_date=self.today + datetime.timedelta(days=9))
        self.task(title='Finished', due_date=yesterday, status='Completed')
        call_command('send_due_reminders', stdout=open(os.devnull, 'w'))
        messages = list(Notification.objects.filter(message__contains='Late').values_list('message', flat=True))
        self.assertEqual(len(messages), 1)
        self.assertTrue(Notification.objects.filter(message__startswith='Due today: "Today"').exists())
        self.assertFalse(Notification.objects.filter(message__contains='Later').exists())
        self.assertFalse(Notification.objects.filter(message__contains='Finished').exists())
        count = Notification.objects.count()
        call_command('send_due_reminders', stdout=open(os.devnull, 'w'))
        self.assertEqual(Notification.objects.count(), count)


# ---------------------------------------------------------------- projects & labels

class ProjectLabelTests(Base):
    def test_manager_creates_edits_deletes_project(self):
        self.login(self.manager)
        self.client.post(reverse('project_add'), {'name': 'Website', 'description': 'Redo'})
        project = Project.objects.get()
        self.assertEqual(project.created_by, self.manager)
        self.client.post(reverse('project_edit', args=[project.pk]), {'name': 'Website v2', 'description': ''})
        project.refresh_from_db()
        self.assertEqual(project.name, 'Website v2')
        task = self.task(project=project)
        self.assertEqual(self.client.get(reverse('project_delete', args=[project.pk])).status_code, 200)
        self.client.post(reverse('project_delete', args=[project.pk]))
        self.assertFalse(Project.objects.exists())
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

    def test_duplicate_project_name_rejected(self):
        Project.objects.create(name='Same')
        self.login(self.manager)
        response = self.client.post(reverse('project_add'), {'name': 'Same', 'description': ''})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Project.objects.count(), 1)

    def test_project_list_shows_progress(self):
        project = Project.objects.create(name='P')
        self.task(project=project, status='Completed', progress=100)
        self.task(project=project, progress=0)
        self.login(self.owner)
        response = self.client.get(reverse('project_list'))
        item = response.context['projects'][0]
        self.assertEqual((item.total, item.done, item.progress), (2, 1, 50))

    def test_labels(self):
        self.login(self.manager)
        self.client.post(reverse('label_add'), {'name': 'Bug', 'color': '#ff0000'})
        self.assertEqual(Label.objects.get().color, '#ff0000')
        self.client.post(reverse('label_add'), {'name': 'Bad', 'color': 'red'})  # galat color
        self.client.post(reverse('label_add'), {'name': 'Bug', 'color': '#00ff00'})  # duplicate name
        self.assertEqual(Label.objects.count(), 1)
        self.assertEqual(self.client.get(reverse('label_list')).status_code, 200)
        self.client.post(reverse('label_delete', args=[Label.objects.get().pk]))
        self.assertEqual(Label.objects.count(), 0)


# ---------------------------------------------------------------- reports, analytics

class ReportTests(Base):
    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def test_csv_export_and_filter(self):
        self.task(title='Plain', status='Pending')
        self.task(title='=HYPERLINK("http://evil")', status='Completed')
        response = self.client.get(reverse('task_report'), {'export': 'csv'})
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="tasks-all.csv"', response['Content-Disposition'])
        text = response.content.decode('utf-8-sig')
        self.assertTrue(text.startswith('ID,Title,Project,Assigned to,Status'))
        self.assertIn("'=HYPERLINK", text)  # formula injection roka gaya

        filtered = self.client.get(reverse('task_report'), {'export': 'csv', 'status': 'Pending'})
        self.assertIn('filename="tasks-pending.csv"', filtered['Content-Disposition'])
        body = filtered.content.decode('utf-8-sig')
        self.assertIn('Plain', body)
        self.assertNotIn('HYPERLINK', body)

    def test_report_page_has_new_controls(self):
        response = self.client.get(reverse('task_report'))
        self.assertContains(response, 'Download CSV')
        self.assertContains(response, 'Print or save as PDF')

    def test_analytics_numbers(self):
        yesterday = self.today - datetime.timedelta(days=1)
        self.task(title='Open late', due_date=yesterday)
        done = self.task(title='Done', status='Completed')
        done.refresh_from_db()
        Task.objects.filter(pk=done.pk).update(created_at=done.completed_at - datetime.timedelta(days=2, hours=3, minutes=30))
        TimeEntry.objects.create(task=done, user=self.owner, minutes=125)

        response = self.client.get(reverse('analytics'))
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual((ctx['total_tasks'], ctx['overdue_total'], ctx['completed_count']), (2, 1, 1))
        self.assertEqual(ctx['total_time'], '2h 5m')
        self.assertEqual(ctx['average_completion'], '2d 3h')
        person = ctx['people'][0]
        self.assertEqual((person['username'], person['open'], person['done'], person['overdue'], person['time']), ('owner', 1, 1, 1, '2h 5m'))
        self.assertEqual(len(ctx['weekly']), 8)
        self.assertEqual(ctx['weekly'][-1]['completed'], 1)
        self.assertEqual(dict((l, c) for l, c, _ in ctx['status_rows'])['Completed'], 1)

    def test_analytics_with_no_data(self):
        response = self.client.get(reverse('analytics'))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['average_completion'])


# ---------------------------------------------------------------- JSON API

class ApiTests(Base):
    def test_anonymous_gets_json_401(self):
        for url in (reverse('api_task_list'), reverse('api_task_detail', args=[1])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {'error': 'Authentication required.'})
        self.assertEqual(self.client.post(reverse('api_task_status', args=[1])).status_code, 401)

    def test_list_with_filters_and_paging(self):
        self.login(self.owner)
        for i in range(5):
            self.task(title=f'T{i}', priority='High' if i % 2 else 'Low')
        data = self.client.get(reverse('api_task_list'), {'priority': 'High', 'page_size': 1}).json()
        self.assertEqual((data['count'], data['pages'], len(data['results'])), (2, 2, 1))
        self.assertEqual(self.client.get(reverse('api_task_list'), {'page_size': 'abc', 'page': 'zzz'}).status_code, 200)
        self.assertEqual(len(self.client.get(reverse('api_task_list'), {'page_size': 100000}).json()['results']), 5)

    def test_detail_shape(self):
        self.login(self.owner)
        label = Label.objects.create(name='Bug')
        task = self.task(title='Shape', labels=[label], due_date=datetime.date(2026, 1, 2))
        data = self.client.get(reverse('api_task_detail', args=[task.pk])).json()
        self.assertEqual(data['title'], 'Shape')
        self.assertEqual(data['labels'], ['Bug'])
        self.assertEqual(data['due_date'], '2026-01-02')
        self.assertEqual(data['assigned_to'], 'owner')
        self.assertEqual(self.client.get(reverse('api_task_detail', args=[99999])).status_code, 404)

    def post_status(self, task, payload, form=False):
        url = reverse('api_task_status', args=[task.pk])
        if form:
            return self.client.post(url, payload)
        return self.client.post(url, json.dumps(payload), content_type='application/json')

    def test_status_change_json_and_form(self):
        self.login(self.owner)
        task = self.task(created_by=self.other)
        response = self.post_status(task, {'status': 'In Progress'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'In Progress')
        self.assertTrue(task.activity.filter(message='Changed status from "Pending" to "In Progress"').exists())
        self.assertTrue(Notification.objects.filter(user=self.other, message__contains='In Progress').exists())

        response = self.post_status(task, {'status': 'Completed'}, form=True)
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)

    def test_status_change_spawns_recurrence(self):
        self.login(self.owner)
        task = self.task(recurrence='weekly', due_date=datetime.date(2026, 1, 5))
        self.post_status(task, {'status': 'Completed'})
        self.assertEqual(Task.objects.count(), 2)

    def test_status_same_value_is_noop(self):
        self.login(self.owner)
        task = self.task()
        before = task.activity.count()
        self.assertEqual(self.post_status(task, {'status': 'Pending'}).status_code, 200)
        self.assertEqual(task.activity.count(), before)

    def test_status_errors(self):
        self.login(self.owner)
        task = self.task()
        bad = self.post_status(task, {'status': 'Nonsense'})
        self.assertEqual(bad.status_code, 400)
        self.assertIn('Pending', bad.json()['allowed'])
        self.assertEqual(self.post_status(task, {}).status_code, 400)
        self.assertEqual(self.post_status(task, ['x']).status_code, 400)
        broken = self.client.post(reverse('api_task_status', args=[task.pk]), '{not json', content_type='application/json')
        self.assertEqual(broken.status_code, 400)
        self.assertEqual(self.client.get(reverse('api_task_status', args=[task.pk])).status_code, 405)

    def test_status_requires_edit_permission(self):
        task = self.task()
        self.login(self.other)
        response = self.post_status(task, {'status': 'Completed'})
        self.assertEqual(response.status_code, 403)
        task.refresh_from_db()
        self.assertEqual(task.status, 'Pending')

    def test_csrf_enforced_for_session_users(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        task = self.task()
        response = strict.post(reverse('api_task_status', args=[task.pk]), json.dumps({'status': 'Completed'}), content_type='application/json')
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------- admin

class AdminTests(Base):
    def test_admin_pages_load(self):
        self.login(self.admin)
        task = self.task()
        for url in [
            reverse('admin:core_task_changelist'), reverse('admin:core_task_change', args=[task.pk]),
            reverse('admin:core_project_changelist'), reverse('admin:core_label_changelist'),
            reverse('admin:core_comment_changelist'), reverse('admin:core_attachment_changelist'),
            reverse('admin:core_timeentry_changelist'), reverse('admin:core_activitylog_changelist'),
            reverse('admin:core_notification_changelist'),
            reverse('admin:auth_user_changelist'), reverse('admin:auth_user_add'),
            reverse('admin:auth_user_change', args=[self.manager.pk]),
            reverse('admin:auth_user_change', args=[self.owner.pk]),
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_role_column_in_user_list(self):
        self.login(self.admin)
        response = self.client.get(reverse('admin:auth_user_changelist'))
        self.assertContains(response, 'Manager')
        self.assertContains(response, 'Admin (superuser)')


class MigrationDataTests(TestCase):
    def test_backfill_function(self):
        from importlib import import_module
        from django.apps import apps
        module = import_module('core.migrations.0008_backfill_completed_at')
        user = User.objects.create_user('u')
        old = Task.objects.create(title='Old', description='d', assigned_to=user, status='Completed')
        Task.objects.filter(pk=old.pk).update(completed_at=None)
        module.backfill_completed_at(apps, None)
        old.refresh_from_db()
        self.assertEqual(old.completed_at, old.created_at)
