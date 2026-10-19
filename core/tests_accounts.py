from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Notification, SignupRequest, UserProfile

PW = 'Zx9-longer-pass-77'


def make(username, role='member', **extra):
    user = User.objects.create_user(username, f'{username}@x.com', PW, **extra)
    UserProfile.objects.update_or_create(user=user, defaults={'role': role})
    return user


def signup_data(**over):
    data = {'username': 'newbie', 'first_name': 'New', 'last_name': 'Bie', 'email': 'newbie@x.com',
            'password1': PW, 'password2': PW, 'website': ''}
    data.update(over)
    return data


class HomeTests(TestCase):
    def test_home_for_employee(self):
        make('emp')
        self.client.login(username='emp', password=PW)
        r = self.client.get(reverse('home'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Check in')

    def test_login_redirects_home(self):
        make('emp')
        r = self.client.post(reverse('login'), {'username': 'emp', 'password': PW})
        self.assertRedirects(r, reverse('home'))

    def test_dashboard_still_works(self):
        make('emp')
        self.client.login(username='emp', password=PW)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_check_in_returns_home_and_ignores_external_next(self):
        make('emp')
        self.client.login(username='emp', password=PW)
        r = self.client.post(reverse('check_in'), {'next': reverse('home')})
        self.assertRedirects(r, reverse('home'))
        r = self.client.post(reverse('check_out'), {'next': 'https://evil.example/'})
        self.assertRedirects(r, reverse('attendance_me'))

    def test_home_needs_login(self):
        r = self.client.get(reverse('home'))
        self.assertEqual(r.status_code, 302)

    def test_each_employee_sees_only_own_tasks(self):
        from .models import Task
        a, b = make('a'), make('b')
        Task.objects.create(title='A-only task', assigned_to=a, created_by=a)
        Task.objects.create(title='B-only task', assigned_to=b, created_by=b)
        self.client.login(username='b', password=PW)
        r = self.client.get(reverse('home'))
        self.assertContains(r, 'B-only task')
        self.assertNotContains(r, 'A-only task')


class SignupTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_login_page_links_signup(self):
        self.assertContains(self.client.get(reverse('login')), reverse('signup'))

    def test_signup_creates_inactive_user_with_request(self):
        hr = make('boss', 'hr')
        r = self.client.post(reverse('signup'), signup_data())
        self.assertRedirects(r, reverse('signup_done'))
        user = User.objects.get(username='newbie')
        self.assertFalse(user.is_active)
        self.assertTrue(SignupRequest.objects.filter(user=user).exists())
        self.assertTrue(Notification.objects.filter(user=hr).exists())
        self.assertFalse(self.client.login(username='newbie', password=PW))

    @override_settings(SIGNUP_MODE='open')
    def test_open_mode_activates(self):
        self.client.post(reverse('signup'), signup_data())
        user = User.objects.get(username='newbie')
        self.assertTrue(user.is_active)
        self.assertFalse(SignupRequest.objects.exists())
        self.assertTrue(self.client.login(username='newbie', password=PW))

    @override_settings(SIGNUP_MODE='off')
    def test_off_mode(self):
        self.assertEqual(self.client.get(reverse('signup')).status_code, 403)
        self.assertEqual(self.client.post(reverse('signup'), signup_data()).status_code, 403)
        self.assertFalse(User.objects.filter(username='newbie').exists())
        self.assertNotContains(self.client.get(reverse('login')), reverse('signup'))

    def test_honeypot_creates_nothing(self):
        r = self.client.post(reverse('signup'), signup_data(website='http://spam'))
        self.assertRedirects(r, reverse('signup_done'))
        self.assertFalse(User.objects.filter(username='newbie').exists())

    @override_settings(SIGNUP_ALLOWED_DOMAINS=['corp.com'])
    def test_domain_restriction(self):
        r = self.client.post(reverse('signup'), signup_data())
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username='newbie').exists())
        r = self.client.post(reverse('signup'), signup_data(email='n@corp.com'))
        self.assertRedirects(r, reverse('signup_done'))

    def test_duplicate_email_and_weak_password_rejected(self):
        make('exists')
        r = self.client.post(reverse('signup'), signup_data(email='EXISTS@x.com'))
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse('signup'), signup_data(password1='12345678', password2='12345678'))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username='newbie').exists())

    @override_settings(SIGNUP_MAX_PER_HOUR=2)
    def test_throttle(self):
        for i in range(2):
            self.client.post(reverse('signup'), signup_data(username=f'u{i}', email=f'u{i}@x.com'))
        r = self.client.post(reverse('signup'), signup_data(username='u9', email='u9@x.com'))
        self.assertEqual(r.status_code, 429)
        self.assertFalse(User.objects.filter(username='u9').exists())

    def test_logged_in_user_redirected(self):
        make('emp')
        self.client.login(username='emp', password=PW)
        self.assertRedirects(self.client.get(reverse('signup')), reverse('home'))


class ApprovalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.hr = make('boss', 'hr')
        self.emp = make('emp')
        self.client.post(reverse('signup'), signup_data())
        self.req = SignupRequest.objects.get()

    def test_hr_approves(self):
        self.client.login(username='boss', password=PW)
        self.assertContains(self.client.get(reverse('signup_list')), 'newbie')
        r = self.client.post(reverse('signup_approve', args=[self.req.pk]))
        self.assertRedirects(r, reverse('signup_list'))
        user = User.objects.get(username='newbie')
        self.assertTrue(user.is_active)
        self.assertFalse(SignupRequest.objects.exists())
        self.client.logout()
        self.assertTrue(self.client.login(username='newbie', password=PW))

    def test_hr_rejects_deletes_user(self):
        self.client.login(username='boss', password=PW)
        self.client.post(reverse('signup_reject', args=[self.req.pk]))
        self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_reject_never_deletes_active_user(self):
        user = self.req.user
        user.is_active = True
        user.save()
        self.client.login(username='boss', password=PW)
        self.client.post(reverse('signup_reject', args=[self.req.pk]))
        self.assertTrue(User.objects.filter(username='newbie').exists())

    def test_employee_cannot_approve(self):
        self.client.login(username='emp', password=PW)
        self.assertEqual(self.client.get(reverse('signup_list')).status_code, 403)
        self.assertEqual(self.client.post(reverse('signup_approve', args=[self.req.pk])).status_code, 403)
        self.assertFalse(User.objects.get(username='newbie').is_active)

    def test_get_does_not_approve(self):
        self.client.login(username='boss', password=PW)
        self.assertEqual(self.client.get(reverse('signup_approve', args=[self.req.pk])).status_code, 405)

    def test_hr_todo_shows_pending(self):
        self.client.login(username='boss', password=PW)
        self.assertContains(self.client.get(reverse('home')), 'sign up request')


class AddEmployeeTests(TestCase):
    def setUp(self):
        self.hr = make('boss', 'hr')
        self.client.login(username='boss', password=PW)

    def data(self, **over):
        d = {'username': 'kiran', 'first_name': 'Kiran', 'last_name': '', 'email': 'kiran@x.com',
             'role': 'member', 'department': '', 'designation': 'Dev', 'joining_date': '', 'password': PW}
        d.update(over)
        return d

    def test_creates_account_with_forced_change(self):
        r = self.client.post(reverse('employee_add'), self.data())
        self.assertRedirects(r, reverse('hr_employees'))
        user = User.objects.get(username='kiran')
        self.assertTrue(user.is_active)
        self.assertTrue(user.profile.must_change_password)
        self.assertEqual(user.profile.role, 'member')

    def test_hr_cannot_create_hr_or_admin(self):
        r = self.client.post(reverse('employee_add'), self.data(role='hr'))
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse('employee_add'), self.data(role='admin'))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username='kiran').exists())

    def test_admin_can_create_hr(self):
        User.objects.create_superuser('root', 'r@x.com', PW)
        self.client.login(username='root', password=PW)
        self.client.post(reverse('employee_add'), self.data(role='hr'))
        self.assertEqual(User.objects.get(username='kiran').profile.role, 'hr')

    def test_validation(self):
        make('taken')
        for bad in (self.data(username='TAKEN'), self.data(email='taken@x.com'), self.data(password='123')):
            self.assertEqual(self.client.post(reverse('employee_add'), bad).status_code, 200)
        self.assertFalse(User.objects.filter(username='kiran').exists())

    def test_employee_forbidden(self):
        make('emp')
        self.client.login(username='emp', password=PW)
        self.assertEqual(self.client.get(reverse('employee_add')).status_code, 403)
        self.assertEqual(self.client.post(reverse('employee_add'), self.data()).status_code, 403)

    def test_forced_password_change_flow(self):
        self.client.post(reverse('employee_add'), self.data())
        self.client.logout()
        self.assertTrue(self.client.login(username='kiran', password=PW))
        r = self.client.get(reverse('home'))
        self.assertRedirects(r, reverse('password_change'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('password_change')).status_code, 200)
        self.assertEqual(self.client.get(reverse('leave_list')).status_code, 302)
        new = 'Another-pass-4321'
        r = self.client.post(reverse('password_change'), {
            'old_password': PW, 'new_password1': new, 'new_password2': new})
        self.assertRedirects(r, reverse('password_change_done'))
        self.assertFalse(User.objects.get(username='kiran').profile.must_change_password)
        self.assertEqual(self.client.get(reverse('home')).status_code, 200)

    def test_logout_allowed_while_forced(self):
        self.client.post(reverse('employee_add'), self.data())
        self.client.logout()
        self.client.login(username='kiran', password=PW)
        r = self.client.post(reverse('logout'))
        self.assertEqual(r.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)
