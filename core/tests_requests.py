import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from . import payroll
from .celebrations import upcoming
from .forms import MyContactForm
from .models import (
    Asset, AssetAssignment, Attendance, AttendanceRequest, Claim, EmployeeChecklist, Notification, Payslip,
    Resignation, UserProfile,
)
from .tests_hr import HRBase

D = datetime.date
TMP_MEDIA = override_settings(MEDIA_ROOT='/tmp/office_hub_test_media')


class RequestBase(HRBase):
    def setUp(self):
        super().setUp()
        profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        profile.reporting_manager = self.manager
        profile.save()
        self.today = timezone.localdate()

    def day(self, offset):
        return self.today + datetime.timedelta(days=offset)


class AccessTests(RequestBase):
    def test_login_required(self):
        for name in ('claim_list', 'claim_add', 'request_list', 'request_add', 'resign', 'exit_list'):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn('/login/', r['Location'])

    def test_pages_open(self):
        for user in (self.owner, self.manager, self.hr, self.admin):
            self.login(user)
            for name in ('claim_list', 'claim_add', 'request_list', 'request_add', 'resign'):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, (user.username, name))

    def test_exit_list_hr_only(self):
        for user in (self.owner, self.manager):
            self.login(user)
            self.assertEqual(self.client.get(reverse('exit_list')).status_code, 403)
        for user in (self.hr, self.admin):
            self.login(user)
            self.assertEqual(self.client.get(reverse('exit_list')).status_code, 200)

    def test_nav_links(self):
        self.login(self.owner)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Claims', body)
        self.assertNotIn('>Exits<', body.replace(' ', '').replace('\n', ''))
        self.login(self.hr)
        self.assertIn('Exits', self.client.get(reverse('dashboard')).content.decode())


@TMP_MEDIA
class ClaimTests(RequestBase):
    def file_claim(self, user, **extra):
        self.login(user)
        data = {'title': 'Cab to client', 'category': 'travel', 'amount': '450.50',
                'incurred_on': self.day(-2).isoformat(), 'note': ''}
        data.update(extra)
        return self.client.post(reverse('claim_add'), data)

    def test_file_and_notify_reporting_manager(self):
        self.assertEqual(self.file_claim(self.owner).status_code, 302)
        claim = Claim.objects.get()
        self.assertEqual((claim.user, claim.status, claim.amount), (self.owner, 'submitted', Decimal('450.50')))
        self.assertTrue(Notification.objects.filter(user=self.manager, message__contains='Cab to client').exists())
        self.assertFalse(Notification.objects.filter(user=self.hr).exists())

    def test_no_manager_goes_to_hr(self):
        self.file_claim(self.other)
        self.assertTrue(Notification.objects.filter(user=self.hr).exists())

    def test_validation(self):
        for extra in ({'amount': '0'}, {'amount': '-5'}, {'amount': 'abc'}, {'title': '  '},
                      {'incurred_on': self.day(1).isoformat()}, {'incurred_on': self.day(-400).isoformat()},
                      {'category': 'nope'}, {'receipt': SimpleUploadedFile('x.exe', b'x')}):
            self.assertEqual(self.file_claim(self.owner, **extra).status_code, 200, extra)
        self.assertEqual(Claim.objects.count(), 0)

    def test_receipt_upload(self):
        self.file_claim(self.owner, receipt=SimpleUploadedFile('bill.pdf', b'%PDF-1.4 x'))
        self.assertTrue(Claim.objects.get().receipt.name.startswith('claims/'))

    def test_review_permissions(self):
        self.file_claim(self.owner)
        claim = Claim.objects.get()
        for user in (self.other,):  # sadharan employee
            self.login(user)
            self.client.post(reverse('claim_approve', args=[claim.pk]))
            claim.refresh_from_db()
            self.assertEqual(claim.status, 'submitted')
        self.login(self.owner)  # apna khud ka
        self.client.post(reverse('claim_approve', args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'submitted')
        # Manager jo reporting manager nahi hai
        stranger = User.objects.create_user('stranger', 's@example.com', 'pass12345')
        UserProfile.objects.create(user=stranger, role='manager')
        self.login(stranger)
        self.client.post(reverse('claim_approve', args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'submitted')
        self.login(self.manager)
        self.client.post(reverse('claim_approve', args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.reviewed_by), ('approved', self.manager))
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='approved').exists())

    def test_hr_reviews_anyone_and_queue_scoping(self):
        self.file_claim(self.owner, title='Mine')
        self.file_claim(self.other, title='Other person')
        self.login(self.manager)
        queue = [c.title for c in self.client.get(reverse('claim_list')).context['to_review']]
        self.assertEqual(queue, ['Mine'])
        self.login(self.hr)
        queue = sorted(c.title for c in self.client.get(reverse('claim_list')).context['to_review'])
        self.assertEqual(queue, ['Mine', 'Other person'])
        self.login(self.owner)
        self.assertEqual(len(self.client.get(reverse('claim_list')).context['to_review']), 0)

    def test_hr_cannot_approve_own_but_admin_can(self):
        self.file_claim(self.hr)
        claim = Claim.objects.get()
        self.client.post(reverse('claim_approve', args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'submitted')
        self.file_claim(self.admin, title='Admin claim')
        mine = Claim.objects.get(title='Admin claim')
        self.client.post(reverse('claim_approve', args=[mine.pk]))
        mine.refresh_from_db()
        self.assertEqual(mine.status, 'approved')

    def test_reject_and_double_actions(self):
        self.file_claim(self.owner)
        claim = Claim.objects.get()
        self.login(self.manager)
        self.client.post(reverse('claim_reject', args=[claim.pk]), {'note': 'No bill'})
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.review_note), ('rejected', 'No bill'))
        self.client.post(reverse('claim_approve', args=[claim.pk]))  # rejected ko seedha approve nahi
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'rejected')
        self.assertEqual(self.client.get(reverse('claim_approve', args=[claim.pk])).status_code, 405)

    def test_delete_rules(self):
        self.file_claim(self.owner)
        claim = Claim.objects.get()
        self.login(self.other)
        self.client.post(reverse('claim_delete', args=[claim.pk]))
        self.assertEqual(Claim.objects.count(), 1)
        self.login(self.owner)
        self.client.post(reverse('claim_delete', args=[claim.pk]))
        self.assertEqual(Claim.objects.count(), 0)
        self.file_claim(self.owner)
        claim = Claim.objects.get()
        claim.status = 'approved'
        claim.save()
        self.client.post(reverse('claim_delete', args=[claim.pk]))  # approved ko employee nahi hata sakta
        self.assertEqual(Claim.objects.count(), 1)

    def test_only_own_claims_listed(self):
        self.file_claim(self.other, title='Secret')
        self.login(self.owner)
        self.assertNotContains(self.client.get(reverse('claim_list')), 'Secret')


class ReimbursementPayrollTests(RequestBase):
    YEAR, MONTH = 2026, 1

    def setUp(self):
        super().setUp()
        self.salary(self.owner)

    def claim(self, amount='500', on=D(2026, 1, 10), status='approved', user=None):
        return Claim.objects.create(user=user or self.owner, title='Cab', amount=Decimal(amount), incurred_on=on, status=status)

    def slip(self):
        payroll.generate_month([self.owner], self.YEAR, self.MONTH, self.hr)
        return Payslip.objects.get(user=self.owner, year=self.YEAR, month=self.MONTH)

    def test_approved_claim_added_to_net(self):
        base = self.slip()
        net_before = base.net
        base.delete()
        claim = self.claim('500')
        slip = self.slip()
        claim.refresh_from_db()
        self.assertEqual(slip.reimbursement, Decimal('500.00'))
        self.assertEqual(slip.net, net_before + Decimal('500.00'))
        self.assertEqual(claim.payslip, slip)
        # Gross aur deductions par asar nahi
        self.assertEqual(slip.gross, Decimal('40000.00'))

    def test_only_approved_and_not_future_months(self):
        self.claim('100', status='submitted')
        self.claim('200', status='rejected')
        self.claim('300', on=D(2026, 2, 5))
        self.claim('50', on=D(2025, 12, 20))  # pichhle mahine ka bhi us mahine mein mil jata hai
        self.assertEqual(self.slip().reimbursement, Decimal('50.00'))

    def test_claim_paid_once(self):
        claim = self.claim('500')
        first = self.slip()
        payroll.generate_month([self.owner], self.YEAR, self.MONTH + 1, self.hr)
        second = Payslip.objects.get(user=self.owner, month=self.MONTH + 1)
        self.assertEqual(first.reimbursement, Decimal('500.00'))
        self.assertEqual(second.reimbursement, Decimal('0.00'))
        claim.refresh_from_db()
        self.assertEqual(claim.payslip, first)

    def test_recalculate_picks_up_new_claim_and_keeps_old(self):
        self.claim('500')
        slip = self.slip()
        self.claim('250')
        payroll.recalculate(slip)
        slip.refresh_from_db()
        self.assertEqual(slip.reimbursement, Decimal('750.00'))
        self.assertEqual(Claim.objects.filter(payslip=slip).count(), 2)
        payroll.recalculate(slip)  # dobara chalane se double nahi
        slip.refresh_from_db()
        self.assertEqual(slip.reimbursement, Decimal('750.00'))

    def test_deleting_draft_frees_claims(self):
        claim = self.claim('500')
        slip = self.slip()
        self.login(self.hr)
        self.client.post(reverse('payslip_delete', args=[slip.pk]))
        claim.refresh_from_db()
        self.assertIsNone(claim.payslip)
        self.assertEqual(claim.status, 'approved')
        self.assertEqual(self.slip().reimbursement, Decimal('500.00'))

    def test_cannot_reject_claim_in_payslip(self):
        claim = self.claim('500')
        self.slip()
        self.login(self.hr)
        self.client.post(reverse('claim_reject', args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'approved')

    def test_can_reject_approved_unpaid_claim(self):
        claim = self.claim('500')
        self.login(self.hr)
        self.client.post(reverse('claim_reject', args=[claim.pk]), {'note': 'duplicate'})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'rejected')
        self.assertEqual(self.slip().reimbursement, Decimal('0.00'))

    def test_claim_status_label_and_payslip_page(self):
        claim = self.claim('500')
        slip = self.slip()
        self.login(self.owner)
        self.assertContains(self.client.get(reverse('claim_list')), 'In a draft payslip')
        slip.status = 'published'
        slip.save()
        self.assertContains(self.client.get(reverse('claim_list')), 'Paid in payslip')
        page = self.client.get(reverse('payslip_detail', args=[slip.pk]))
        self.assertContains(page, 'Reimbursements')
        self.assertContains(page, 'Cab')

    def test_payroll_page_and_csv(self):
        self.claim('500')
        self.slip()
        self.login(self.hr)
        self.assertContains(self.client.get(reverse('payroll'), {'month': '2026-01'}), 'Reimb.')
        csv_text = self.client.get(reverse('payroll_export'), {'month': '2026-01'}).content.decode('utf-8-sig')
        self.assertIn('Reimbursement', csv_text.splitlines()[0])
        self.assertIn('500.00', csv_text)

    def test_other_users_claims_not_mixed(self):
        self.salary(self.other)
        self.claim('999', user=self.other)
        self.assertEqual(self.slip().reimbursement, Decimal('0.00'))


class AttendanceRequestTests(RequestBase):
    def ask(self, user, kind='regularize', day=None, start='09:30', end='18:00', reason='Forgot to check in'):
        self.login(user)
        data = {'kind': kind, 'date': (day or self.day(-1)).isoformat(), 'reason': reason}
        if kind == 'regularize':
            data.update(check_in_time=start, check_out_time=end)
        return self.client.post(reverse('request_add'), data)

    def test_regularize_flow_creates_attendance(self):
        self.assertEqual(self.ask(self.owner).status_code, 302)
        item = AttendanceRequest.objects.get()
        self.assertEqual(item.status, 'pending')
        self.assertTrue(Notification.objects.filter(user=self.manager).exists())
        self.login(self.manager)
        self.client.post(reverse('request_approve', args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual(item.status, 'approved')
        record = Attendance.objects.get(user=self.owner)
        self.assertEqual(record.date, self.day(-1))
        self.assertEqual(timezone.localtime(record.check_in).strftime('%H:%M'), '09:30')
        self.assertEqual(timezone.localtime(record.check_out).strftime('%H:%M'), '18:00')
        self.assertEqual(record.edited_by, self.manager)
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='approved').exists())

    def test_fixes_missing_checkout(self):
        day = self.day(-1)
        in_time = timezone.make_aware(datetime.datetime.combine(day, datetime.time(9, 0)))
        Attendance.objects.create(user=self.owner, date=day, check_in=in_time)
        self.ask(self.owner, end='17:30')
        item = AttendanceRequest.objects.get()
        self.login(self.hr)
        self.client.post(reverse('request_approve', args=[item.pk]))
        record = Attendance.objects.get(user=self.owner, date=day)
        self.assertEqual(timezone.localtime(record.check_out).strftime('%H:%M'), '17:30')
        self.assertEqual(Attendance.objects.filter(user=self.owner).count(), 1)

    def test_complete_day_cannot_be_overwritten(self):
        day = self.day(-1)
        make = lambda h: timezone.make_aware(datetime.datetime.combine(day, datetime.time(h, 0)))
        Attendance.objects.create(user=self.owner, date=day, check_in=make(9), check_out=make(17))
        self.ask(self.owner)
        item = AttendanceRequest.objects.get()
        self.login(self.manager)
        self.client.post(reverse('request_approve', args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
        self.assertEqual(Attendance.objects.get().check_out, make(17))

    def test_validation(self):
        cases = [
            dict(day=self.day(1)),                            # aane wala din fix nahi hota
            dict(day=self.day(-45)),                          # bahut purana
            dict(start='18:00', end='09:00'),                 # ulta
            dict(start='09:00', end='09:00'),
            dict(reason='  '),
            dict(start='', end='18:00'),
            dict(start='09:00', end=''),
            dict(kind='wfh', day=self.day(-1)),               # WFH beete din ke liye nahi
            dict(kind='wfh', day=self.day(60)),
        ]
        for case in cases:
            self.assertEqual(self.ask(self.owner, **case).status_code, 200, case)
        self.assertEqual(AttendanceRequest.objects.count(), 0)

    def test_today_future_checkout_rejected(self):
        now = timezone.localtime()
        if now.hour >= 22:
            self.skipTest('too late in the day for this check')
        later = (now + datetime.timedelta(hours=1)).strftime('%H:%M')
        self.assertEqual(self.ask(self.owner, day=self.today, start='00:01', end=later).status_code, 200)
        self.assertEqual(AttendanceRequest.objects.count(), 0)

    def test_duplicate_request_blocked_but_allowed_after_reject(self):
        self.ask(self.owner)
        self.assertEqual(self.ask(self.owner).status_code, 200)
        self.assertEqual(AttendanceRequest.objects.count(), 1)
        item = AttendanceRequest.objects.get()
        self.login(self.manager)
        self.client.post(reverse('request_reject', args=[item.pk]), {'note': 'No'})
        self.assertEqual(self.ask(self.owner).status_code, 302)
        self.assertEqual(AttendanceRequest.objects.count(), 2)

    def test_wfh_marks_checkin(self):
        self.assertEqual(self.ask(self.owner, kind='wfh', day=self.today, reason='Plumber').status_code, 302)
        item = AttendanceRequest.objects.get()
        self.assertIsNone(item.check_in_time)
        self.login(self.manager)
        self.client.post(reverse('request_approve', args=[item.pk]))
        self.login(self.owner)
        self.client.post(reverse('check_in'))
        self.assertEqual(Attendance.objects.get(user=self.owner).mode, 'wfh')
        self.assertContains(self.client.get(reverse('attendance_me')), 'WFH')

    def test_wfh_approved_after_checkin_updates_mode(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('check_in'))
        self.assertEqual(Attendance.objects.get().mode, 'office')
        self.ask(self.owner, kind='wfh', day=self.today)
        item = AttendanceRequest.objects.get()
        self.login(self.hr)
        self.client.post(reverse('request_approve', args=[item.pk]))
        self.assertEqual(Attendance.objects.get().mode, 'wfh')

    def test_unapproved_wfh_does_not_change_mode(self):
        self.ask(self.owner, kind='wfh', day=self.today)
        self.login(self.owner)
        self.client.post(reverse('check_in'))
        self.assertEqual(Attendance.objects.get().mode, 'office')

    def test_review_permissions_and_queue(self):
        self.ask(self.owner)
        item = AttendanceRequest.objects.get()
        for user in (self.other, self.owner):
            self.login(user)
            self.client.post(reverse('request_approve', args=[item.pk]))
            item.refresh_from_db()
            self.assertEqual(item.status, 'pending', user.username)
        self.login(self.other)
        self.assertEqual(len(self.client.get(reverse('request_list')).context['to_review']), 0)
        self.login(self.manager)
        self.assertEqual(len(self.client.get(reverse('request_list')).context['to_review']), 1)
        self.client.post(reverse('request_reject', args=[item.pk]), {'note': 'Not true'})
        item.refresh_from_db()
        self.assertEqual((item.status, item.review_note), ('rejected', 'Not true'))
        self.client.post(reverse('request_approve', args=[item.pk]))  # decided ho chuka
        item.refresh_from_db()
        self.assertEqual(item.status, 'rejected')
        self.assertEqual(Attendance.objects.count(), 0)

    def test_cancel(self):
        self.ask(self.owner)
        item = AttendanceRequest.objects.get()
        self.login(self.other)
        self.assertEqual(self.client.post(reverse('request_cancel', args=[item.pk])).status_code, 404)
        self.login(self.owner)
        self.client.post(reverse('request_cancel', args=[item.pk]))
        self.assertEqual(AttendanceRequest.objects.count(), 0)

    def test_summary_counts_fixed_day(self):
        self.ask(self.owner, day=self.day(-1))
        item = AttendanceRequest.objects.get()
        self.login(self.hr)
        self.client.post(reverse('request_approve', args=[item.pk]))
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('attendance_me')).status_code, 200)


class ResignationTests(RequestBase):
    def submit(self, user, days=30, reason='Better offer'):
        self.login(user)
        return self.client.post(reverse('resign'), {'proposed_last_day': self.day(days).isoformat(), 'reason': reason})

    def test_submit_and_notify_hr(self):
        self.assertEqual(self.submit(self.owner).status_code, 302)
        item = Resignation.objects.get()
        self.assertEqual((item.user, item.status), (self.owner, 'pending'))
        self.assertTrue(Notification.objects.filter(user=self.hr, message__contains='resignation').exists())
        self.assertContains(self.client.get(reverse('resign')), 'Waiting for HR')

    def test_validation_and_single_active(self):
        self.assertEqual(self.submit(self.owner, days=-1).status_code, 200)
        self.assertEqual(self.submit(self.owner, days=800).status_code, 200)
        self.assertEqual(Resignation.objects.count(), 0)
        self.submit(self.owner)
        self.submit(self.owner)
        self.assertEqual(Resignation.objects.count(), 1)

    def test_withdraw_then_resubmit(self):
        self.submit(self.owner)
        self.client.post(reverse('resign_withdraw'))
        self.assertEqual(Resignation.objects.get().status, 'withdrawn')
        self.client.post(reverse('resign_withdraw'))  # kuch nahi bacha
        self.submit(self.owner, days=45)
        self.assertEqual(Resignation.objects.count(), 2)
        self.assertEqual(Resignation.objects.filter(status='pending').count(), 1)

    def test_accept_starts_offboarding(self):
        self.submit(self.owner)
        item = Resignation.objects.get()
        self.login(self.hr)
        last = self.day(20)
        self.client.post(reverse('exit_accept', args=[item.pk]), {'last_working_day': last.isoformat(), 'hr_note': 'OK'})
        item.refresh_from_db()
        self.assertEqual((item.status, item.last_working_day, item.decided_by), ('accepted', last, self.hr))
        self.assertTrue(EmployeeChecklist.objects.filter(user=self.owner, kind='offboarding').exists())
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='accepted').exists())
        self.client.post(reverse('exit_accept', args=[item.pk]), {'last_working_day': self.day(5).isoformat()})
        item.refresh_from_db()
        self.assertEqual(item.last_working_day, last)  # dobara decide nahi

    def test_accept_defaults_to_proposed_day(self):
        self.submit(self.owner, days=10)
        item = Resignation.objects.get()
        self.login(self.hr)
        self.client.post(reverse('exit_accept', args=[item.pk]), {})
        item.refresh_from_db()
        self.assertEqual(item.last_working_day, self.day(10))

    def test_reject(self):
        self.submit(self.owner)
        item = Resignation.objects.get()
        self.login(self.hr)
        self.client.post(reverse('exit_reject', args=[item.pk]), {'hr_note': 'Please stay'})
        item.refresh_from_db()
        self.assertEqual((item.status, item.hr_note), ('rejected', 'Please stay'))
        self.assertFalse(EmployeeChecklist.objects.exists())
        self.submit(self.owner)  # rejected ke baad dobara de sakta hai
        self.assertEqual(Resignation.objects.count(), 2)

    def test_only_hr_decides_and_not_own(self):
        self.submit(self.owner)
        item = Resignation.objects.get()
        for user in (self.owner, self.manager):
            self.login(user)
            self.assertEqual(self.client.post(reverse('exit_accept', args=[item.pk]), {}).status_code, 403)
            self.assertEqual(self.client.post(reverse('exit_complete', args=[item.pk])).status_code, 403)
        self.submit(self.hr)
        own = Resignation.objects.get(user=self.hr)
        self.client.post(reverse('exit_accept', args=[own.pk]), {})
        own.refresh_from_db()
        self.assertEqual(own.status, 'pending')
        self.login(self.admin)
        self.client.post(reverse('exit_accept', args=[own.pk]), {})
        own.refresh_from_db()
        self.assertEqual(own.status, 'accepted')

    def accepted(self, last_offset=0):
        self.submit(self.owner)
        item = Resignation.objects.get()
        self.login(self.hr)
        self.client.post(reverse('exit_accept', args=[item.pk]), {'last_working_day': self.day(last_offset).isoformat()})
        return Resignation.objects.get()

    def test_complete_disables_login_and_sets_exit_date(self):
        item = self.accepted(0)
        self.client.post(reverse('exit_complete', args=[item.pk]))
        item.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(item.status, 'completed')
        self.assertFalse(self.owner.is_active)
        self.assertEqual(self.owner.profile.exit_date, self.today)
        self.client.logout()
        self.assertFalse(self.client.login(username='owner', password='pass12345'))

    def test_complete_blocked_before_last_day(self):
        item = self.accepted(10)
        self.client.post(reverse('exit_complete', args=[item.pk]))
        item.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(item.status, 'accepted')
        self.assertTrue(self.owner.is_active)

    def test_complete_blocked_with_assets(self):
        item = self.accepted(0)
        asset = Asset.objects.create(name='Laptop', asset_tag='LAP-1', status='assigned')
        AssetAssignment.objects.create(asset=asset, user=self.owner)
        self.client.post(reverse('exit_complete', args=[item.pk]))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        AssetAssignment.objects.update(returned_on=self.today)
        self.client.post(reverse('exit_complete', args=[item.pk]))
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_active)

    def test_complete_requires_accepted(self):
        self.submit(self.owner)
        item = Resignation.objects.get()
        self.login(self.hr)
        self.client.post(reverse('exit_complete', args=[item.pk]))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_last_superuser_protected(self):
        self.submit(self.admin, days=0)
        item = Resignation.objects.get()
        # Doosra HR/admin accept karta hai (admin khud nahi kar sakta, HR kar sakta hai)
        self.login(self.hr)
        self.client.post(reverse('exit_accept', args=[item.pk]), {'last_working_day': self.today.isoformat()})
        self.client.post(reverse('exit_complete', args=[item.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        User.objects.create_superuser('root2', 'r2@example.com', 'pass12345')
        self.client.post(reverse('exit_complete', args=[item.pk]))
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)

    def test_no_payslip_after_exit_month(self):
        self.salary(self.owner)
        profile = self.owner.profile
        profile.exit_date = D(2026, 2, 10)
        profile.save()
        self.assertTrue(payroll.eligible(self.owner, 2026, 2))   # chhodne wale mahine ki slip banti hai
        self.assertFalse(payroll.eligible(self.owner, 2026, 3))
        self.assertTrue(payroll.eligible(self.owner, 2026, 1))

    def test_exit_list_shows_pending_work(self):
        self.submit(self.owner)
        self.login(self.hr)
        page = self.client.get(reverse('exit_list'))
        self.assertContains(page, 'owner')
        self.assertEqual(len(page.context['rows']), 1)


class CelebrationTests(RequestBase):
    def profile(self, user, **kw):
        profile, _ = UserProfile.objects.get_or_create(user=user)
        for k, v in kw.items():
            setattr(profile, k, v)
        profile.save()

    def test_birthday_window(self):
        today = D(2026, 6, 10)
        self.profile(self.owner, date_of_birth=D(1990, 6, 15))
        self.profile(self.other, date_of_birth=D(1990, 6, 30))
        events = upcoming(today, days=14)
        names = [(e['user'].username, e['kind'], e['days_away']) for e in events]
        self.assertIn(('owner', 'birthday', 5), names)
        self.assertNotIn('other', [n[0] for n in names])
        self.assertEqual(upcoming(D(2026, 6, 15))[0]['days_away'], 0)

    def test_year_wrap(self):
        self.profile(self.owner, date_of_birth=D(1990, 1, 3))
        events = upcoming(D(2026, 12, 28), days=14)
        self.assertEqual([(e['date'], e['days_away']) for e in events if e['user'] == self.owner], [(D(2027, 1, 3), 6)])

    def test_leap_day_birthday(self):
        self.profile(self.owner, date_of_birth=D(2000, 2, 29))
        self.assertEqual([e['date'] for e in upcoming(D(2027, 2, 20)) if e['user'] == self.owner], [D(2027, 2, 28)])
        self.assertEqual([e['date'] for e in upcoming(D(2028, 2, 20)) if e['user'] == self.owner], [D(2028, 2, 29)])

    def test_anniversary_years(self):
        self.profile(self.owner, joining_date=D(2023, 6, 12))
        self.profile(self.other, joining_date=D(2026, 6, 12))  # pehla saal poora nahi
        events = [e for e in upcoming(D(2026, 6, 10)) if e['kind'] == 'anniversary']
        self.assertEqual([(e['user'].username, e['years']) for e in events], [('owner', 3)])

    def test_inactive_and_missing_excluded(self):
        self.profile(self.owner, date_of_birth=D(1990, 6, 11), joining_date=D(2020, 6, 11))
        self.owner.is_active = False
        self.owner.save()
        self.assertEqual(upcoming(D(2026, 6, 10)), [])

    def test_sorted_by_date(self):
        self.profile(self.owner, date_of_birth=D(1990, 6, 20))
        self.profile(self.other, date_of_birth=D(1991, 6, 12))
        self.assertEqual([e['user'].username for e in upcoming(D(2026, 6, 10))], ['other', 'owner'])

    def test_dashboard_card(self):
        self.profile(self.other, date_of_birth=self.today.replace(year=1990) if not (self.today.month == 2 and self.today.day == 29) else D(1990, 2, 28))
        self.login(self.owner)
        page = self.client.get(reverse('dashboard'))
        self.assertContains(page, 'Birthdays and anniversaries')
        self.assertContains(page, 'Today')

    def test_dob_validation(self):
        profile = self.owner.profile
        for bad in (self.day(1), D(1850, 1, 1), self.today.replace(year=self.today.year - 5) if not (self.today.month == 2 and self.today.day == 29) else D(2020, 2, 28)):
            form = MyContactForm({'date_of_birth': bad.isoformat()}, instance=profile)
            self.assertFalse(form.is_valid(), bad)
        form = MyContactForm({'date_of_birth': D(1990, 5, 5).isoformat()}, instance=profile)
        self.assertTrue(form.is_valid())

    def test_employee_and_hr_can_set_dob(self):
        self.login(self.owner)
        self.client.post(reverse('my_profile'), {'date_of_birth': '1992-03-04', 'phone': '', 'emergency_name': '', 'emergency_phone': ''})
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.date_of_birth, D(1992, 3, 4))
        self.login(self.hr)
        self.assertContains(self.client.get(reverse('employee_edit', args=[self.owner.pk])), 'date_of_birth')
