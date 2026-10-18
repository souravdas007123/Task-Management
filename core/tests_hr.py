import datetime
from decimal import Decimal

from django.contrib import admin as django_admin
from django.contrib.auth.models import User
from django.urls import reverse

from .forms import EmployeeForm, PayslipAdjustForm, SalaryStructureForm
from .hr import check_leave_request, leave_balance, working_days
from .models import (
    Department, Holiday, LeavePolicy, LeaveRequest, Notification, Payslip, SalaryStructure, UserProfile,
)
from .payroll import generate_month, recalculate, structure_for
from .tests_features import Base

JAN = (2026, 1)  # 22 working days


class HRBase(Base):
    def setUp(self):
        super().setUp()
        self.hr = User.objects.create_user('hruser', 'hr@example.com', 'pass12345')
        UserProfile.objects.create(user=self.hr, role='hr')

    def salary(self, user, effective=datetime.date(2025, 4, 1), **kw):
        values = dict(basic=Decimal('30000'), hra=Decimal('10000'), allowances=Decimal('0'),
                      pf_percent=Decimal('12'), tax=Decimal('1000'), other_deduction=Decimal('0'))
        values.update(kw)
        return SalaryStructure.objects.create(user=user, effective_from=effective, **values)

    def leave(self, user, start, end, kind='annual', status='approved'):
        return LeaveRequest.objects.create(user=user, kind=kind, start_date=start, end_date=end, status=status)


class AccessTests(HRBase):
    urls = ['hr_employees', 'payroll', 'payroll_export', 'leave_policy', 'department_list']

    def test_only_hr_and_admin(self):
        for user in (self.owner, self.manager):
            self.login(user)
            for name in self.urls:
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, (user.username, name))
            self.assertEqual(self.client.get(reverse('employee_edit', args=[self.owner.pk])).status_code, 403)
            self.assertEqual(self.client.get(reverse('salary_history', args=[self.owner.pk])).status_code, 403)
        for user in (self.hr, self.admin):
            self.login(user)
            for name in self.urls:
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, (user.username, name))
            self.assertEqual(self.client.get(reverse('salary_history', args=[self.owner.pk])).status_code, 200)

    def test_posts_blocked_for_others(self):
        slip = Payslip.objects.create(user=self.owner, year=2026, month=1, basic=1)
        sal = self.salary(self.owner)
        self.login(self.manager)
        for name, args in (('payroll_generate', []), ('payroll_publish_all', []), ('payslip_publish', [slip.pk]),
                           ('payslip_delete', [slip.pk]), ('salary_delete', [sal.pk]), ('holiday_add', []),
                           ('department_delete', [1])):
            self.assertEqual(self.client.post(reverse(name, args=args)).status_code, 403, name)
        self.assertTrue(Payslip.objects.filter(pk=slip.pk).exists())
        self.assertTrue(SalaryStructure.objects.filter(pk=sal.pk).exists())

    def test_hr_role_has_no_manager_powers(self):
        self.login(self.hr)
        self.assertEqual(self.client.get(reverse('budget_overview')).status_code, 403)
        self.assertEqual(self.client.get(reverse('audit_log')).status_code, 403)

    def test_salary_models_not_in_admin_site(self):
        from .models import Payslip as P, SalaryStructure as S
        self.assertNotIn(P, django_admin.site._registry)
        self.assertNotIn(S, django_admin.site._registry)


class PeopleTests(HRBase):
    def test_directory_hides_phone_and_salary_from_members(self):
        dept = Department.objects.create(name='Sales')
        UserProfile.objects.update_or_create(user=self.owner, defaults={
            'designation': 'Account Executive', 'department': dept, 'phone': '98300 11111'})
        self.salary(self.owner, basic=Decimal('77777'))
        self.login(self.other)
        r = self.client.get(reverse('people'))
        self.assertContains(r, 'Account Executive')
        self.assertContains(r, 'Sales')
        self.assertNotContains(r, '98300 11111')
        self.assertNotContains(r, '77777')
        self.login(self.hr)
        self.assertContains(self.client.get(reverse('people')), '98300 11111')

    def test_search_and_bad_params(self):
        UserProfile.objects.update_or_create(user=self.owner, defaults={'designation': 'Cashier'})
        self.login(self.other)
        r = self.client.get(reverse('people') + '?q=cashier')
        self.assertEqual(len(r.context['people']), 1)
        for q in ('?department=abc', '?department=99999999999999', '?q=%00'):
            self.assertEqual(self.client.get(reverse('people') + q).status_code, 200, q)

    def test_org_chart_and_loop_safe(self):
        UserProfile.objects.update_or_create(user=self.owner, defaults={'reporting_manager': self.manager})
        UserProfile.objects.update_or_create(user=self.other, defaults={'reporting_manager': self.owner})
        self.login(self.other)
        rows = self.client.get(reverse('org_chart')).context['rows']
        depth = {r['user'].username: r['depth'] for r in rows}
        self.assertEqual((depth['boss'], depth['owner'], depth['other']), (0, 1, 2))
        # loop: boss -> other (boss reports to other), sab phir bhi sirf ek baar dikhein
        UserProfile.objects.update_or_create(user=self.manager, defaults={'reporting_manager': self.other})
        rows = self.client.get(reverse('org_chart')).context['rows']
        names = [r['user'].username for r in rows]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), User.objects.filter(is_active=True).count())

    def test_form_rejects_loop_and_self(self):
        boss_profile = UserProfile.objects.get_or_create(user=self.manager)[0]
        UserProfile.objects.update_or_create(user=self.owner, defaults={'reporting_manager': self.manager})
        form = EmployeeForm({'employment_type': 'full_time', 'reporting_manager': self.owner.pk}, instance=boss_profile)
        self.assertFalse(form.is_valid())
        self.assertIn('reporting_manager', form.errors)
        self.assertNotIn(self.manager, form.fields['reporting_manager'].queryset)

    def test_employee_edit_and_empty_employee_id(self):
        self.login(self.hr)
        data = {'designation': 'Clerk', 'employment_type': 'intern', 'employee_id': '', 'phone': '12345 6789'}
        self.assertRedirects(self.client.post(reverse('employee_edit', args=[self.owner.pk]), data), reverse('hr_employees'))
        self.client.post(reverse('employee_edit', args=[self.other.pk]), data)  # do blank IDs takrate nahi
        self.assertIsNone(self.owner.profile.employee_id)
        self.assertEqual(UserProfile.objects.get(user=self.other).designation, 'Clerk')
        bad = dict(data, phone='abc', probation_end='2020-01-01', joining_date='2021-01-01')
        r = self.client.post(reverse('employee_edit', args=[self.owner.pk]), bad)
        self.assertEqual(r.status_code, 200)

    def test_duplicate_employee_id_rejected(self):
        self.login(self.hr)
        self.client.post(reverse('employee_edit', args=[self.owner.pk]), {'employment_type': 'full_time', 'employee_id': 'E1'})
        r = self.client.post(reverse('employee_edit', args=[self.other.pk]), {'employment_type': 'full_time', 'employee_id': 'E1'})
        self.assertEqual(r.status_code, 200)

    def test_my_profile_contact_only(self):
        self.login(self.owner)
        r = self.client.post(reverse('my_profile'), {
            'phone': '99999 00000', 'emergency_name': 'Mom', 'emergency_phone': '88888 11111',
            'designation': 'CEO', 'role': 'admin'})
        self.assertRedirects(r, reverse('my_profile'))
        profile = UserProfile.objects.get(user=self.owner)
        self.assertEqual((profile.phone, profile.emergency_name), ('99999 00000', 'Mom'))
        self.assertEqual((profile.designation, profile.role), ('', 'member'))
        self.assertEqual(self.client.post(reverse('my_profile'), {'phone': 'x'}).status_code, 200)


class LeaveBalanceTests(HRBase):
    def test_working_days_and_holidays(self):
        mon, sun = datetime.date(2026, 1, 12), datetime.date(2026, 1, 18)
        self.assertEqual(working_days(mon, sun), 5)
        self.assertEqual(working_days(datetime.date(2026, 1, 3), datetime.date(2026, 1, 4)), 0)
        Holiday.objects.create(date=datetime.date(2026, 1, 14), name='Fest')
        Holiday.objects.create(date=datetime.date(2026, 1, 17), name='Sat fest')  # weekend: dobara nahi ginta
        self.assertEqual(working_days(mon, sun), 4)
        self.assertEqual(working_days(sun, mon), 0)

    def test_balance_counts_approved_and_pending(self):
        self.leave(self.owner, datetime.date(2026, 1, 12), datetime.date(2026, 1, 14))
        self.leave(self.owner, datetime.date(2026, 2, 2), datetime.date(2026, 2, 3), status='pending')
        self.leave(self.owner, datetime.date(2026, 3, 2), datetime.date(2026, 3, 2), status='rejected')
        row = next(r for r in leave_balance(self.owner, 2026) if r['kind'] == 'annual')
        self.assertEqual((row['quota'], row['used'], row['pending'], row['remaining']), (18, 3, 2, 13))

    def test_quota_blocks_request_and_weekend_only(self):
        mon = datetime.date(2026, 1, 12)
        msg = check_leave_request(self.owner, 'casual', mon, mon + datetime.timedelta(days=13))  # 10 days > 6
        self.assertIn('only 6', msg)
        self.assertIn('weekends', check_leave_request(self.owner, 'annual', datetime.date(2026, 1, 3), datetime.date(2026, 1, 4)))
        self.assertIsNone(check_leave_request(self.owner, 'unpaid', mon, mon + datetime.timedelta(days=60)))

    def test_balance_remaining_shrinks(self):
        self.leave(self.owner, datetime.date(2026, 1, 5), datetime.date(2026, 1, 9), kind='casual')  # 5 of 6
        self.assertIn('only 1', check_leave_request(self.owner, 'casual', datetime.date(2026, 2, 2), datetime.date(2026, 2, 3)))
        self.assertIsNone(check_leave_request(self.owner, 'casual', datetime.date(2026, 2, 2), datetime.date(2026, 2, 2)))

    def test_year_boundary_counts_each_year(self):
        # 28 Dec 2026 - 3 Jan 2027: 4 din 2026 ke, 1 din 2027 ka
        self.leave(self.owner, datetime.date(2026, 12, 28), datetime.date(2027, 1, 3))
        used = lambda y: next(r for r in leave_balance(self.owner, y) if r['kind'] == 'annual')['used']
        self.assertEqual((used(2026), used(2027)), (4, 1))

    def test_custom_policy(self):
        LeavePolicy.objects.create(kind='casual', days_per_year=1, is_paid=True)
        mon = datetime.date(2026, 1, 12)
        self.assertIn('only 1', check_leave_request(self.owner, 'casual', mon, mon + datetime.timedelta(days=1)))

    def test_leave_form_shows_error_and_page_shows_balance(self):
        self.login(self.owner)
        mon = datetime.date(2026, 1, 12)
        r = self.client.post(reverse('leave_add'), {
            'kind': 'casual', 'start_date': mon.isoformat(), 'end_date': (mon + datetime.timedelta(days=13)).isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'only 6')
        self.assertEqual(LeaveRequest.objects.count(), 0)
        page = self.client.get(reverse('leave_list'))
        self.assertContains(page, 'leave balance')
        self.assertEqual(len(page.context['balance']), 5)


class SalaryTests(HRBase):
    def test_structure_effective_dating(self):
        old = self.salary(self.owner, effective=datetime.date(2025, 4, 1))
        new = self.salary(self.owner, effective=datetime.date(2026, 2, 15), basic=Decimal('40000'))
        self.assertEqual(structure_for(self.owner, 2026, 1), old)
        self.assertEqual(structure_for(self.owner, 2026, 2), new)  # poore mahine par lagu
        self.assertIsNone(structure_for(self.owner, 2025, 3))

    def test_form_validation(self):
        base = {'effective_from': '2026-01-01', 'basic': '30000', 'hra': '0', 'allowances': '0',
                'pf_percent': '12', 'tax': '0', 'other_deduction': '0'}
        self.assertTrue(SalaryStructureForm(base).is_valid())
        for change in ({'basic': '0'}, {'basic': '-5'}, {'pf_percent': '101'}, {'tax': '999999'}, {'hra': '-1'}):
            self.assertFalse(SalaryStructureForm({**base, **change}).is_valid(), change)

    def test_history_add_and_delete(self):
        self.login(self.hr)
        data = {'effective_from': '2026-01-01', 'basic': '25000', 'hra': '5000', 'allowances': '0',
                'pf_percent': '12', 'tax': '0', 'other_deduction': '0', 'note': 'Joining'}
        r = self.client.post(reverse('salary_history', args=[self.owner.pk]), data)
        self.assertRedirects(r, reverse('salary_history', args=[self.owner.pk]))
        s = SalaryStructure.objects.get()
        self.assertEqual((s.created_by, s.gross), (self.hr, Decimal('30000')))
        self.assertContains(self.client.get(reverse('hr_employees')), '30,000.00')
        self.client.post(reverse('salary_delete', args=[s.pk]))
        self.assertFalse(SalaryStructure.objects.exists())

    def test_member_cannot_see_own_structure_page(self):
        self.salary(self.owner)
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('salary_history', args=[self.owner.pk])).status_code, 403)


class PayrollTests(HRBase):
    def setUp(self):
        super().setUp()
        self.salary(self.owner)

    def test_calculation_with_unpaid_leave(self):
        self.leave(self.owner, datetime.date(2026, 1, 12), datetime.date(2026, 1, 13), kind='unpaid')
        self.leave(self.owner, datetime.date(2026, 1, 20), datetime.date(2026, 1, 21), kind='annual')  # paid, katauti nahi
        self.leave(self.owner, datetime.date(2026, 1, 26), datetime.date(2026, 1, 26), kind='unpaid', status='pending')
        created, skipped = generate_month(User.objects.filter(pk=self.owner.pk), *JAN, generated_by=self.hr)
        self.assertEqual((created, skipped), (1, 0))
        slip = Payslip.objects.get()
        self.assertEqual((slip.working_days, slip.unpaid_days), (22, 2))
        self.assertEqual(slip.gross, Decimal('40000.00'))
        self.assertEqual(slip.pf, Decimal('3600.00'))
        self.assertEqual(slip.leave_deduction, Decimal('3636.36'))
        self.assertEqual(slip.net, Decimal('31763.64'))
        self.assertEqual(slip.status, 'draft')

    def test_holiday_changes_working_days_and_partial_overlap(self):
        Holiday.objects.create(date=datetime.date(2026, 1, 26), name='Republic Day')
        # leave 30 Dec - 2 Jan: sirf 1-2 Jan January mein
        self.leave(self.owner, datetime.date(2025, 12, 30), datetime.date(2026, 1, 2), kind='unpaid')
        generate_month(User.objects.filter(pk=self.owner.pk), *JAN, generated_by=self.hr)
        slip = Payslip.objects.get()
        self.assertEqual((slip.working_days, slip.unpaid_days), (21, 2))

    def test_generate_skips_existing_no_salary_and_future_joiners(self):
        self.salary(self.other)
        UserProfile.objects.update_or_create(user=self.other, defaults={'joining_date': datetime.date(2026, 2, 10)})
        users = User.objects.filter(is_active=True)
        created, _ = generate_month(users, *JAN, generated_by=self.hr)
        self.assertEqual(created, 1)  # owner; other abhi join nahi hua, baaki ka salary nahi
        again, skipped = generate_month(users, *JAN, generated_by=self.hr)
        self.assertEqual(again, 0)
        self.assertEqual(Payslip.objects.count(), 1)

    def test_snapshot_not_changed_by_new_salary_and_recalculate_keeps_bonus(self):
        generate_month(User.objects.filter(pk=self.owner.pk), *JAN, generated_by=self.hr)
        slip = Payslip.objects.get()
        slip.bonus = Decimal('500')
        slip.save()
        self.salary(self.owner, effective=datetime.date(2026, 1, 20), basic=Decimal('60000'))
        slip.refresh_from_db()
        self.assertEqual(slip.basic, Decimal('30000.00'))
        self.assertTrue(recalculate(slip))
        slip.refresh_from_db()
        self.assertEqual((slip.basic, slip.bonus), (Decimal('60000.00'), Decimal('500')))

    def test_publish_flow_and_employee_visibility(self):
        self.login(self.hr)
        self.client.post(reverse('payroll_generate'), {'month': '2026-01'})
        slip = Payslip.objects.get()
        # Draft: employee ko 404, list khali
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('payslip_detail', args=[slip.pk])).status_code, 404)
        self.assertEqual(len(self.client.get(reverse('my_payslips')).context['payslips']), 0)
        self.login(self.hr)
        self.assertEqual(self.client.get(reverse('payslip_detail', args=[slip.pk])).status_code, 200)
        self.client.post(reverse('payslip_publish', args=[slip.pk]))
        slip.refresh_from_db()
        self.assertEqual(slip.status, 'published')
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='January 2026').exists())
        # Published: owner dekh sakta hai, doosra nahi, manager bhi nahi
        self.login(self.owner)
        r = self.client.get(reverse('payslip_detail', args=[slip.pk]))
        self.assertContains(r, 'Net pay')
        self.assertContains(r, '35,400.00')
        for user in (self.other, self.manager):
            self.login(user)
            self.assertEqual(self.client.get(reverse('payslip_detail', args=[slip.pk])).status_code, 404, user.username)
        # Published ko edit/delete/recalculate nahi
        self.login(self.hr)
        self.client.post(reverse('payslip_delete', args=[slip.pk]))
        self.client.post(reverse('payslip_recalculate', args=[slip.pk]))
        self.assertEqual(self.client.get(reverse('payslip_adjust', args=[slip.pk])).status_code, 302)
        self.assertTrue(Payslip.objects.filter(pk=slip.pk).exists())
        self.client.post(reverse('payslip_reopen', args=[slip.pk]))
        slip.refresh_from_db()
        self.assertEqual((slip.status, slip.published_at), ('draft', None))

    def test_publish_all_and_totals(self):
        self.salary(self.other, basic=Decimal('20000'), hra=Decimal('0'), tax=Decimal('0'))
        self.login(self.hr)
        self.client.post(reverse('payroll_generate'), {'month': '2026-01'})
        r = self.client.get(reverse('payroll') + '?month=2026-01')
        self.assertEqual(r.context['drafts'], 2)
        self.assertEqual(r.context['totals']['gross'], Decimal('60000.00'))
        self.client.post(reverse('payroll_publish_all'), {'month': '2026-01'})
        self.assertEqual(Payslip.objects.filter(status='published').count(), 2)

    def test_adjust_bonus_and_negative_net_rejected(self):
        generate_month(User.objects.filter(pk=self.owner.pk), *JAN, generated_by=self.hr)
        slip = Payslip.objects.get()
        self.assertTrue(PayslipAdjustForm({'bonus': '1000', 'extra_deduction': '0', 'adjustment_note': 'Diwali'}, instance=slip).is_valid())
        self.assertFalse(PayslipAdjustForm({'bonus': '0', 'extra_deduction': '999999', 'adjustment_note': ''}, instance=slip).is_valid())
        self.assertFalse(PayslipAdjustForm({'bonus': '-1', 'extra_deduction': '0', 'adjustment_note': ''}, instance=slip).is_valid())
        self.login(self.hr)
        self.client.post(reverse('payslip_adjust', args=[slip.pk]), {'bonus': '1000', 'extra_deduction': '250', 'adjustment_note': 'Diwali'})
        slip.refresh_from_db()
        self.assertEqual(slip.net, Decimal('35400.00') + Decimal('1000') - Decimal('250'))

    def test_month_param_and_export(self):
        self.login(self.hr)
        for q in ('?month=garbage', '?month=2026-13', '?month=0001-01', '?month=2026-1-1', ''):
            self.assertEqual(self.client.get(reverse('payroll') + q).status_code, 200, q)
        self.assertEqual(self.client.post(reverse('payroll_generate'), {'month': 'zzz'}).status_code, 302)
        u = User.objects.create_user('=evil()', 'e@example.com', 'pass12345')
        self.salary(u)
        generate_month(User.objects.filter(pk=u.pk), *JAN, generated_by=self.hr)
        r = self.client.get(reverse('payroll_export') + '?month=2026-01')
        self.assertEqual(r.status_code, 200)
        self.assertIn("'=evil()", r.content.decode('utf-8'))

    def test_duplicate_payslip_prevented_by_database(self):
        from django.db import IntegrityError, transaction
        generate_month(User.objects.filter(pk=self.owner.pk), *JAN, generated_by=self.hr)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payslip.objects.create(user=self.owner, year=2026, month=1, basic=1)


class HolidayPolicyDepartmentTests(HRBase):
    def test_holidays_visible_to_all_editable_by_hr(self):
        Holiday.objects.create(date=datetime.date(2026, 8, 15), name='Independence Day')
        self.login(self.owner)
        r = self.client.get(reverse('holiday_list') + '?year=2026')
        self.assertContains(r, 'Independence Day')
        self.assertNotContains(r, 'Add a holiday')
        self.assertEqual(self.client.post(reverse('holiday_add'), {'date': '2026-01-26', 'name': 'x'}).status_code, 403)
        self.login(self.hr)
        self.client.post(reverse('holiday_add'), {'date': '2026-01-26', 'name': 'Republic Day'})
        self.client.post(reverse('holiday_add'), {'date': '2026-01-26', 'name': 'Duplicate'})
        self.assertEqual(Holiday.objects.filter(date=datetime.date(2026, 1, 26)).count(), 1)
        for q in ('?year=abc', '?year=99999', '?year=-5'):
            self.assertEqual(self.client.get(reverse('holiday_list') + q).status_code, 200, q)
        self.client.post(reverse('holiday_delete', args=[Holiday.objects.get(name='Republic Day').pk]))
        self.assertFalse(Holiday.objects.filter(name='Republic Day').exists())

    def test_leave_policy_save_and_validation(self):
        self.login(self.hr)
        data = {f'days_{k}': '0' for k in ('annual', 'sick', 'casual', 'unpaid', 'other')}
        data.update({'days_annual': '24', 'paid_annual': 'on', 'days_sick': '10', 'paid_sick': 'on'})
        self.client.post(reverse('leave_policy'), data)
        annual = LeavePolicy.objects.get(kind='annual')
        self.assertEqual((annual.days_per_year, annual.is_paid), (24, True))
        self.assertFalse(LeavePolicy.objects.get(kind='casual').is_paid)  # tick nahi hai -> unpaid
        before = LeavePolicy.objects.count()
        self.client.post(reverse('leave_policy'), {**data, 'days_annual': '-3'})
        self.client.post(reverse('leave_policy'), {**data, 'days_sick': 'abc'})
        self.client.post(reverse('leave_policy'), {**data, 'days_casual': '9999'})
        self.assertEqual(LeavePolicy.objects.get(kind='annual').days_per_year, 24)
        self.assertEqual(LeavePolicy.objects.count(), before)

    def test_unpaid_policy_drives_payroll(self):
        self.salary(self.owner)
        LeavePolicy.objects.create(kind='casual', days_per_year=6, is_paid=False)
        self.leave(self.owner, datetime.date(2026, 1, 12), datetime.date(2026, 1, 12), kind='casual')
        generate_month(User.objects.filter(pk=self.owner.pk), *JAN, generated_by=self.hr)
        self.assertEqual(Payslip.objects.get().unpaid_days, 1)

    def test_departments(self):
        self.login(self.hr)
        self.client.post(reverse('department_list'), {'name': 'Finance'})
        self.assertEqual(self.client.post(reverse('department_list'), {'name': 'Finance'}).status_code, 200)
        dept = Department.objects.get(name='Finance')
        UserProfile.objects.update_or_create(user=self.owner, defaults={'department': dept})
        self.client.post(reverse('department_delete', args=[dept.pk]))
        self.assertIsNone(UserProfile.objects.get(user=self.owner).department)


class RegressionTests(HRBase):
    def test_all_new_pages_render_for_admin(self):
        self.salary(self.owner)
        generate_month(User.objects.filter(pk=self.owner.pk), *JAN, generated_by=self.admin)
        slip = Payslip.objects.get()
        self.login(self.admin)
        for name, args in (('people', []), ('org_chart', []), ('my_profile', []), ('holiday_list', []),
                           ('my_payslips', []), ('payslip_detail', [slip.pk]), ('hr_employees', []),
                           ('payroll', []), ('leave_policy', []), ('department_list', []),
                           ('employee_edit', [self.owner.pk]), ('salary_history', [self.owner.pk]),
                           ('payslip_adjust', [slip.pk]), ('dashboard', []), ('leave_list', [])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_sidebar_links_by_role(self):
        self.login(self.owner)
        r = self.client.get(reverse('dashboard'))
        self.assertContains(r, reverse('people'))
        self.assertContains(r, reverse('my_payslips'))
        self.assertNotContains(r, 'href="' + reverse('hr_employees') + '"')
        self.login(self.hr)
        self.assertContains(self.client.get(reverse('dashboard')), 'href="' + reverse('hr_employees') + '"')
