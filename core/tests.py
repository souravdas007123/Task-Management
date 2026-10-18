from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import Task


class TaskTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='pass12345')

    def make_task(self, **kwargs):
        defaults = dict(title='Sample', description='desc', assigned_to=self.user)
        defaults.update(kwargs)
        return Task.objects.create(**defaults)


class AuthTests(TaskTestBase):
    def test_pages_redirect_to_login_when_anonymous(self):
        task = self.make_task()
        for name, args in [('dashboard', []), ('add_task', []), ('task_report', []), ('update_task', [task.pk])]:
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn(reverse('login'), response['Location'], name)


class ModelTests(TaskTestBase):
    def test_progress_must_be_between_0_and_100(self):
        for bad in (-1, 101):
            task = Task(title='x', description='y', assigned_to=self.user, progress=bad)
            with self.assertRaises(ValidationError):
                task.full_clean()

    def test_creator_delete_keeps_task(self):
        creator = User.objects.create_user(username='creator', password='pass12345')
        task = self.make_task(created_by=creator)
        creator.delete()
        task.refresh_from_db()
        self.assertIsNone(task.created_by)


class ViewTests(TaskTestBase):
    def setUp(self):
        super().setUp()
        self.client.login(username='tester', password='pass12345')

    def test_dashboard_counts_include_on_hold(self):
        self.make_task(status='On Hold', progress=0)
        self.make_task(status='Completed', progress=100)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_tasks'], 2)
        self.assertEqual(response.context['on_hold_tasks'], 1)
        self.assertEqual(response.context['completed_tasks'], 1)
        self.assertEqual(response.context['overall_progress'], 50)

    def test_add_task_sets_created_by(self):
        response = self.client.post(reverse('add_task'), {
            'title': 'New', 'description': 'd', 'assigned_to': self.user.pk,
            'status': 'Pending', 'priority': 'Medium', 'progress': 10,
        })
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(Task.objects.get(title='New').created_by, self.user)

    def test_add_task_rejects_invalid_progress(self):
        response = self.client.post(reverse('add_task'), {
            'title': 'Bad', 'description': 'd', 'assigned_to': self.user.pk,
            'status': 'Pending', 'priority': 'Medium', 'progress': 500,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.filter(title='Bad').exists())

    def test_update_task(self):
        task = self.make_task()
        response = self.client.post(reverse('update_task', args=[task.pk]), {
            'title': 'Changed', 'description': 'd', 'assigned_to': self.user.pk,
            'status': 'In Progress', 'priority': 'High', 'progress': 40,
        })
        self.assertRedirects(response, reverse('dashboard'))
        task.refresh_from_db()
        self.assertEqual((task.title, task.status, task.progress), ('Changed', 'In Progress', 40))

    def test_update_missing_task_is_404(self):
        self.assertEqual(self.client.get(reverse('update_task', args=[9999])).status_code, 404)

    def test_report_filters_by_valid_status(self):
        self.make_task(title='A', status='Completed')
        self.make_task(title='B', status='Pending')
        response = self.client.get(reverse('task_report'), {'status': 'Completed'})
        self.assertEqual([t.title for t in response.context['tasks']], ['A'])

    def test_report_ignores_invalid_status(self):
        self.make_task(title='A', status='Completed')
        self.make_task(title='B', status='Pending')
        response = self.client.get(reverse('task_report'), {'status': 'bogus'})
        self.assertEqual(len(response.context['tasks']), 2)
        self.assertIsNone(response.context['status_filter'])
