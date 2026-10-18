import datetime
import os
import shutil
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from . import payroll
from .advances import allocated, balance, emi, is_open, max_amount, open_advance
from .budget import project_budget_summary
from .models import (
    Advance, AdvanceInstallment, EmployeeDocument, Expense, Notification, Payslip, Project, PurchaseRequest, UserProfile,
    Vendor,
)
from .tests_hr import HRBase

D = datetime.date
PRIVATE = '/tmp/office_hub_test_private'


class MoneyBase(HRBase):
    def setUp(self):
        super().setUp()
        profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        profile.reporting_manager = self.manager
        profile.save()
        self.today = timezone.localdate()


# ---------------------------------------------------------------- advances

class AdvanceRequestTests(MoneyBase):
    def setUp(self):
        super().setUp()
        self.salary(self.owner)  # gross 40,000 -> limit 120,000

    def ask(self, user=None, amount='30000', months='3', reason='Rent'):
        self.login(user or self.owner)
        return self.client.post(reverse('advance_add'), {'amount': amount, 'installments': months, 'reason': reason})

    def test_limit_value(self):
        self.assertEqual(max_amount(self.owner), Decimal('120000.00'))
        self.assertIsNone(max_amount(self.other))

    def test_request_notifies_hr(self):
        self.assertEqual(self.ask().status_code, 302)
        advance = Advance.objects.get()
        self.assertEqual((advance.user, advance.status, advance.amount), (self.owner, 'pending', Decimal('30000')))
        self.assertTrue(Notification.objects.filter(user=self.hr, message__contains='advance').exists())

    def test_validation(self):
        for kwargs in ({'amount': '0'}, {'amount': '-1'}, {'amount': 'abc'}, {'amount': '120000.01'},
                       {'months': '0'}, {'months': '37'}, {'months': 'x'}):
            self.assertEqual(self.ask(**kwargs).status_code, 200, kwargs)
        self.assertEqual(Advance.objects.count(), 0)
        self.assertEqual(self.ask(amount='120000').status_code, 302)  # theek limit par chalta hai

    def test_needs_salary_structure(self):
        r = self.ask(user=self.other)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Ask HR')
        self.assertEqual(Advance.objects.count(), 0)

    def test_only_one_open_advance(self):
        self.ask()
        self.assertEqual(self.ask().status_code, 200)
        self.assertEqual(Advance.objects.count(), 1)
        advance = Advance.objects.get()
        advance.status = 'rejected'
        advance.save()
        self.assertEqual(self.ask().status_code, 302)
        self.assertEqual(Advance.objects.count(), 2)

    def test_hr_decisions_and_permissions(self):
        self.ask()
        advance = Advance.objects.get()
        for user in (self.owner, self.manager):
            self.login(user)
            self.assertEqual(self.client.post(reverse('advance_approve', args=[advance.pk])).status_code, 403)
            self.assertEqual(self.client.post(reverse('advance_reject', args=[advance.pk])).status_code, 403)
            self.assertEqual(self.client.post(reverse('advance_close', args=[advance.pk])).status_code, 403)
        self.login(self.hr)
        past = (self.today.replace(day=1) - datetime.timedelta(days=1)).strftime('%Y-%m')
        self.client.post(reverse('advance_approve', args=[advance.pk]), {'first_month': past})
        advance.refresh_from_db()
        self.assertEqual(advance.status, 'pending')  # beeta mahina nahi
        self.client.post(reverse('advance_approve', args=[advance.pk]), {'first_month': 'garbage'})
        advance.refresh_from_db()
        self.assertEqual(advance.status, 'approved')
        nxt = datetime.date(self.today.year + (self.today.month == 12), self.today.month % 12 + 1, 1)
        self.assertEqual(advance.first_month, nxt)
        self.assertEqual(advance.decided_by, self.hr)
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='approved').exists())
        self.client.post(reverse('advance_reject', args=[advance.pk]))  # decide ho chuka
        advance.refresh_from_db()
        self.assertEqual(advance.status, 'approved')

    def test_reject(self):
        self.ask()
        advance = Advance.objects.get()
        self.login(self.hr)
        self.client.post(reverse('advance_reject', args=[advance.pk]), {'note': 'Not now'})
        advance.refresh_from_db()
        self.assertEqual((advance.status, advance.review_note), ('rejected', 'Not now'))

    def test_hr_cannot_approve_own_but_admin_can(self):
        self.salary(self.hr)
        self.ask(user=self.hr)
        advance = Advance.objects.get()
        self.client.post(reverse('advance_approve', args=[advance.pk]))
        advance.refresh_from_db()
        self.assertEqual(advance.status, 'pending')
        self.login(self.admin)
        self.client.post(reverse('advance_approve', args=[advance.pk]))
        advance.refresh_from_db()
        self.assertEqual(advance.status, 'approved')

    def test_cancel_and_close(self):
        self.ask()
        advance = Advance.objects.get()
        self.login(self.other)
        self.assertEqual(self.client.post(reverse('advance_cancel', args=[advance.pk])).status_code, 404)
        self.login(self.owner)
        self.client.post(reverse('advance_cancel', args=[advance.pk]))
        self.assertEqual(Advance.objects.count(), 0)
        self.ask()
        advance = Advance.objects.get()
        self.login(self.hr)
        self.client.post(reverse('advance_approve', args=[advance.pk]))
        self.login(self.owner)
        self.client.post(reverse('advance_cancel', args=[advance.pk]))  # approved ho gaya
        self.assertEqual(Advance.objects.count(), 1)
        self.login(self.hr)
        self.client.post(reverse('advance_close', args=[advance.pk]))
        advance.refresh_from_db()
        self.assertEqual(advance.status, 'closed')
        self.assertIsNone(open_advance(self.owner))

    def test_pages(self):
        self.ask()
        self.login(self.hr)
        r = self.client.get(reverse('advance_list'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context['queue']), 1)
        self.login(self.owner)
        r = self.client.get(reverse('advance_list'))
        self.assertEqual(len(r.context['queue']), 0)
        self.assertEqual(len(r.context['mine']), 1)
        self.login(self.other)
        self.assertEqual(len(self.client.get(reverse('advance_list')).context['mine']), 0)


class AdvancePayrollTests(MoneyBase):
    def setUp(self):
        super().setUp()
        self.salary(self.owner)

    def advance(self, amount='1000', months=3, first=D(2026, 1, 1), status='approved'):
        return Advance.objects.create(user=self.owner, amount=Decimal(amount), installments=months, first_month=first, status=status)

    def slip(self, month):
        payroll.generate_month([self.owner], 2026, month, self.hr)
        return Payslip.objects.get(user=self.owner, year=2026, month=month)

    def test_emi_schedule_sums_exactly(self):
        adv = self.advance('1000', 3)
        self.assertEqual(emi(adv), Decimal('333.33'))
        amounts = []
        for month in (1, 2, 3, 4):
            slip = self.slip(month)
            amounts.append(slip.advance_deduction)
            slip.status = 'published'
            slip.save()
        self.assertEqual(amounts, [Decimal('333.33'), Decimal('333.33'), Decimal('333.34'), Decimal('0.00')])
        self.assertEqual(sum(amounts), Decimal('1000.00'))
        self.assertEqual(balance(adv), Decimal('0.00'))
        self.assertFalse(is_open(adv))

    def test_not_before_first_month(self):
        self.advance('600', 2, first=D(2026, 3, 1))
        self.assertEqual(self.slip(1).advance_deduction, Decimal('0.00'))
        self.assertEqual(self.slip(2).advance_deduction, Decimal('0.00'))
        self.assertEqual(self.slip(3).advance_deduction, Decimal('300.00'))

    def test_only_approved_advances_deducted(self):
        self.advance(status='pending')
        self.advance(status='rejected')
        self.advance(status='closed')
        self.assertEqual(self.slip(1).advance_deduction, Decimal('0.00'))

    def test_net_and_deductions_include_advance(self):
        base = self.slip(1)
        net_before, ded_before = base.net, base.total_deductions
        base.delete()
        self.advance('900', 3)
        slip = self.slip(1)
        self.assertEqual(slip.advance_deduction, Decimal('300.00'))
        self.assertEqual(slip.total_deductions, ded_before + Decimal('300.00'))
        self.assertEqual(slip.net, net_before - Decimal('300.00'))

    def test_recalculate_does_not_double(self):
        self.advance('900', 3)
        slip = self.slip(1)
        for _ in range(3):
            payroll.recalculate(slip)
        slip.refresh_from_db()
        self.assertEqual(slip.advance_deduction, Decimal('300.00'))
        self.assertEqual(AdvanceInstallment.objects.count(), 1)

    def test_deleting_draft_releases_installment(self):
        adv = self.advance('900', 3)
        slip = self.slip(1)
        self.login(self.hr)
        self.client.post(reverse('payslip_delete', args=[slip.pk]))
        self.assertEqual(AdvanceInstallment.objects.count(), 0)
        self.assertEqual(allocated(adv), Decimal('0'))
        self.assertEqual(self.slip(1).advance_deduction, Decimal('300.00'))

    def test_balance_counts_only_published(self):
        adv = self.advance('900', 3)
        slip = self.slip(1)
        self.assertEqual(balance(adv), Decimal('900.00'))
        slip.status = 'published'
        slip.save()
        self.assertEqual(balance(adv), Decimal('600.00'))

    def test_close_early_stops_future_deductions(self):
        adv = self.advance('900', 3)
        first = self.slip(1)
        adv.status = 'closed'
        adv.save()
        self.assertEqual(self.slip(2).advance_deduction, Decimal('0.00'))
        first.refresh_from_db()
        self.assertEqual(first.advance_deduction, Decimal('300.00'))

    def test_out_of_order_generation_is_consistent(self):
        self.advance('900', 3)
        # March pehle bani: us waqt pehle ki koi kisht nahi, to wo pehli kisht (300) hai
        self.assertEqual(self.slip(3).advance_deduction, Decimal('300.00'))
        # Jan baad mein bani to wo bhi apni 300 leti hai; kul 600 abhi tak, 300 baaki
        self.assertEqual(self.slip(1).advance_deduction, Decimal('300.00'))
        self.assertEqual(allocated(Advance.objects.get()), Decimal('600.00'))

    def test_single_installment_advance(self):
        self.advance('500', 1)
        self.assertEqual(self.slip(1).advance_deduction, Decimal('500.00'))
        self.assertEqual(self.slip(2).advance_deduction, Decimal('0.00'))

    def test_two_advances_one_payslip(self):
        self.advance('300', 3)
        self.advance('600', 2)
        self.assertEqual(self.slip(1).advance_deduction, Decimal('400.00'))

    def test_payslip_page_and_csv(self):
        self.advance('900', 3)
        slip = self.slip(1)
        self.login(self.hr)
        self.assertContains(self.client.get(reverse('payslip_detail', args=[slip.pk])), 'Salary advance repayment')
        csv_text = self.client.get(reverse('payroll_export'), {'month': '2026-01'}).content.decode('utf-8-sig')
        self.assertIn('Advance repayment', csv_text.splitlines()[0])
        self.assertIn('300.00', csv_text)

    def test_exit_list_shows_balance(self):
        from .models import Resignation
        self.advance('900', 3)
        Resignation.objects.create(user=self.owner, proposed_last_day=self.today)
        self.login(self.hr)
        self.assertContains(self.client.get(reverse('exit_list')), 'Advance balance')


# ---------------------------------------------------------------- employee documents

@override_settings(PRIVATE_ROOT=PRIVATE)
class VaultTests(MoneyBase):
    def tearDown(self):
        shutil.rmtree(PRIVATE, ignore_errors=True)

    def upload(self, user, name='pan.pdf', **extra):
        self.login(user)
        data = {'kind': 'pan', 'title': '', 'reference': 'ABCDE1234F', 'file': SimpleUploadedFile(name, b'%PDF-1.4 secret')}
        data.update(extra)
        return self.client.post(reverse('vault_add'), data)

    def test_upload_goes_to_private_storage(self):
        self.assertEqual(self.upload(self.owner).status_code, 302)
        doc = EmployeeDocument.objects.get()
        self.assertEqual((doc.user, doc.uploaded_by), (self.owner, self.owner))
        self.assertTrue(os.path.exists(os.path.join(PRIVATE, doc.file.name)))
        with self.assertRaises(ValueError):
            doc.file.url  # koi public URL nahi

    def test_not_under_media_root(self):
        from django.conf import settings
        self.upload(self.owner)
        doc = EmployeeDocument.objects.get()
        self.assertFalse(os.path.exists(os.path.join(settings.MEDIA_ROOT, doc.file.name)))
        self.assertNotIn(os.path.abspath(settings.MEDIA_ROOT), os.path.abspath(doc.file.path))

    def test_extension_and_blank(self):
        self.assertEqual(self.upload(self.owner, name='run.exe').status_code, 200)
        self.assertEqual(self.upload(self.owner, name='page.html').status_code, 200)
        self.assertEqual(self.upload(self.owner, name='big.pdf', file='').status_code, 200)
        self.assertEqual(EmployeeDocument.objects.count(), 0)

    def test_download_permissions(self):
        self.upload(self.owner)
        doc = EmployeeDocument.objects.get()
        url = reverse('vault_download', args=[doc.pk])
        self.client.logout()
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login/', r['Location'])
        for user in (self.other, self.manager):
            self.login(user)
            self.assertEqual(self.client.get(url).status_code, 404, user.username)
        for user in (self.owner, self.hr, self.admin):
            self.login(user)
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, user.username)
            self.assertEqual(b''.join(r.streaming_content), b'%PDF-1.4 secret')
            self.assertIn('attachment', r['Content-Disposition'])
            self.assertEqual(r['X-Content-Type-Options'], 'nosniff')
            self.assertIn('no-store', r['Cache-Control'])

    def test_missing_file_is_404(self):
        self.upload(self.owner)
        doc = EmployeeDocument.objects.get()
        os.remove(doc.file.path)
        self.assertEqual(self.client.get(reverse('vault_download', args=[doc.pk])).status_code, 404)

    def test_listing_is_private(self):
        self.upload(self.owner, reference='SECRET123')
        self.login(self.other)
        self.assertNotContains(self.client.get(reverse('vault_me')), 'SECRET123')
        self.assertEqual(self.client.get(reverse('vault_person', args=[self.owner.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('vault_overview')).status_code, 403)
        self.assertEqual(self.client.get(reverse('vault_person_add', args=[self.owner.pk])).status_code, 403)
        self.login(self.hr)
        self.assertContains(self.client.get(reverse('vault_person', args=[self.owner.pk])), 'SECRET123')

    def test_hr_adds_for_employee_and_notifies(self):
        self.login(self.hr)
        r = self.client.post(reverse('vault_person_add', args=[self.owner.pk]), {
            'kind': 'offer', 'title': 'Offer letter', 'file': SimpleUploadedFile('offer.pdf', b'%PDF-1.4 o')})
        self.assertRedirects(r, reverse('vault_person', args=[self.owner.pk]))
        doc = EmployeeDocument.objects.get()
        self.assertEqual((doc.user, doc.uploaded_by), (self.owner, self.hr))
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='Offer letter').exists())

    def test_verify_and_delete_rules(self):
        self.upload(self.owner)
        doc = EmployeeDocument.objects.get()
        self.login(self.owner)
        self.assertEqual(self.client.post(reverse('vault_verify', args=[doc.pk])).status_code, 403)
        self.login(self.hr)
        self.client.post(reverse('vault_verify', args=[doc.pk]))
        doc.refresh_from_db()
        self.assertTrue(doc.is_verified)
        self.assertEqual(doc.verified_by, self.hr)
        self.login(self.owner)
        self.client.post(reverse('vault_delete', args=[doc.pk]))  # verified: employee nahi hata sakta
        self.assertEqual(EmployeeDocument.objects.count(), 1)
        self.login(self.hr)
        self.client.post(reverse('vault_verify', args=[doc.pk]))  # toggle
        doc.refresh_from_db()
        self.assertFalse(doc.is_verified)

    def test_hr_cannot_verify_own(self):
        self.upload(self.hr)
        doc = EmployeeDocument.objects.get()
        self.client.post(reverse('vault_verify', args=[doc.pk]))
        doc.refresh_from_db()
        self.assertFalse(doc.is_verified)

    def test_delete_removes_file(self):
        self.upload(self.owner)
        doc = EmployeeDocument.objects.get()
        path = doc.file.path
        self.login(self.other)
        self.assertEqual(self.client.post(reverse('vault_delete', args=[doc.pk])).status_code, 404)
        self.login(self.owner)
        self.client.post(reverse('vault_delete', args=[doc.pk]))
        self.assertEqual(EmployeeDocument.objects.count(), 0)
        self.assertFalse(os.path.exists(path))

    def test_expiry_states_and_todos(self):
        self.upload(self.owner, expires_on=(self.today + datetime.timedelta(days=10)).isoformat(), reference='A')
        self.upload(self.owner, name='b.pdf', expires_on=(self.today - datetime.timedelta(days=1)).isoformat(), reference='B')
        self.upload(self.owner, name='c.pdf', expires_on=(self.today + datetime.timedelta(days=200)).isoformat(), reference='C')
        states = sorted(d.state for d in self.client.get(reverse('vault_me')).context['docs'])
        self.assertEqual(states, ['', 'expired', 'soon'])
        todos = [t for t, _ in self.client.get(reverse('dashboard')).context['todo_items']]
        self.assertTrue(any('2 of your documents' in t for t in todos))
        self.login(self.hr)
        overview = self.client.get(reverse('vault_overview'))
        self.assertEqual(len(overview.context['docs']), 2)
        self.assertTrue(any('employee documents' in t for t, _ in self.client.get(reverse('dashboard')).context['todo_items']))

    def test_inactive_employee_not_in_overview(self):
        self.upload(self.owner, expires_on=(self.today - datetime.timedelta(days=5)).isoformat())
        self.owner.is_active = False
        self.owner.save()
        self.login(self.hr)
        self.assertEqual(len(self.client.get(reverse('vault_overview')).context['docs']), 0)

    def test_old_expiry_year_rejected(self):
        self.assertEqual(self.upload(self.owner, expires_on='1990-01-01').status_code, 200)
        self.assertEqual(EmployeeDocument.objects.count(), 0)


# ---------------------------------------------------------------- purchases

class PurchaseTests(MoneyBase):
    def setUp(self):
        super().setUp()
        self.project = Project.objects.create(name='Website', budget=Decimal('50000'))

    def ask(self, user=None, qty=2, cost='1000', **extra):
        self.login(user or self.owner)
        data = {'title': 'Chairs', 'category': 'furniture', 'description': '', 'quantity': qty, 'unit_cost': cost}
        data.update(extra)
        return self.client.post(reverse('purchase_add'), data)

    def act(self, user, name, pk, **data):
        self.login(user)
        return self.client.post(reverse(name, args=[pk]), data)

    def test_request_and_validation(self):
        self.assertEqual(self.ask().status_code, 302)
        item = PurchaseRequest.objects.get()
        self.assertEqual((item.status, item.total), ('pending', Decimal('2000')))
        self.assertTrue(Notification.objects.filter(user=self.manager, message__contains='Chairs').exists())
        for extra in ({'qty': 0}, {'cost': '0'}, {'cost': 'x'}, {'title': ' '}, {'needed_by': '2000-01-01'},
                      {'category': 'zzz'}, {'vendor': 999}):
            self.assertEqual(self.ask(**extra).status_code, 200, extra)
        self.assertEqual(PurchaseRequest.objects.count(), 1)

    def test_below_limit_needs_only_manager(self):
        self.ask()
        item = PurchaseRequest.objects.get()
        self.act(self.manager, 'purchase_approve', item.pk)
        item.refresh_from_db()
        self.assertEqual((item.status, item.level1_by), ('approved', self.manager))
        self.assertTrue(Notification.objects.filter(user=self.hr, message__contains='Ready to order').exists())

    @override_settings(PURCHASE_APPROVAL_LIMIT='1500')
    def test_above_limit_needs_finance(self):
        self.ask()
        item = PurchaseRequest.objects.get()
        self.act(self.manager, 'purchase_approve', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'review')
        self.assertTrue(Notification.objects.filter(user=self.hr, message__contains='finance approval').exists())
        # Manager doosri baar approve nahi kar sakta
        self.act(self.manager, 'purchase_approve', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'review')
        self.act(self.owner, 'purchase_approve', item.pk)
        self.act(self.other, 'purchase_approve', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'review')
        self.act(self.hr, 'purchase_approve', item.pk)
        item.refresh_from_db()
        self.assertEqual((item.status, item.level2_by), ('approved', self.hr))

    @override_settings(PURCHASE_APPROVAL_LIMIT='2000')
    def test_exactly_limit_is_single_step(self):
        self.ask()
        item = PurchaseRequest.objects.get()
        self.act(self.manager, 'purchase_approve', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'approved')

    @override_settings(PURCHASE_APPROVAL_LIMIT='100')
    def test_same_hr_cannot_do_both_levels_but_admin_can(self):
        self.ask(user=self.other)  # reporting manager nahi: HR level 1 karta hai
        item = PurchaseRequest.objects.get()
        self.act(self.hr, 'purchase_approve', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'review')
        self.act(self.hr, 'purchase_approve', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'review')
        self.act(self.admin, 'purchase_approve', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'approved')

    def test_nobody_approves_own(self):
        self.ask(user=self.hr)
        item = PurchaseRequest.objects.get()
        self.act(self.hr, 'purchase_approve', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
        self.act(self.hr, 'purchase_reject', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')

    def test_reject_and_cancel(self):
        self.ask()
        item = PurchaseRequest.objects.get()
        self.act(self.other, 'purchase_reject', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
        self.act(self.manager, 'purchase_reject', item.pk, note='Too costly')
        item.refresh_from_db()
        self.assertEqual((item.status, item.review_note), ('rejected', 'Too costly'))
        self.act(self.manager, 'purchase_approve', item.pk)  # rejected ko approve nahi
        item.refresh_from_db()
        self.assertEqual(item.status, 'rejected')
        self.ask()
        second = PurchaseRequest.objects.exclude(pk=item.pk).get()
        self.act(self.other, 'purchase_cancel', second.pk)
        second.refresh_from_db()
        self.assertEqual(second.status, 'pending')
        self.act(self.owner, 'purchase_cancel', second.pk)
        second.refresh_from_db()
        self.assertEqual(second.status, 'cancelled')

    def approved(self, **kwargs):
        self.ask(**kwargs)
        item = PurchaseRequest.objects.latest('id')
        self.act(self.manager, 'purchase_approve', item.pk)
        item.refresh_from_db()
        return item

    def test_order_and_receive_flow_adds_project_expense(self):
        item = self.approved(project=self.project.pk)
        self.act(self.hr, 'purchase_receive', item.pk)  # abhi order nahi hua
        item.refresh_from_db()
        self.assertEqual(item.status, 'approved')
        vendor = Vendor.objects.create(name='Acme Supplies')
        self.act(self.hr, 'purchase_order', item.pk, vendor=vendor.pk, po_number='PO-7')
        item.refresh_from_db()
        self.assertEqual((item.status, item.vendor, item.po_number), ('ordered', vendor, 'PO-7'))
        self.act(self.hr, 'purchase_receive', item.pk, actual_cost='1850.50')
        item.refresh_from_db()
        self.assertEqual((item.status, item.actual_cost), ('received', Decimal('1850.50')))
        expense = Expense.objects.get()
        self.assertEqual((expense.project, expense.amount, expense.status, expense.category, expense.submitted_by),
                         (self.project, Decimal('1850.50'), 'approved', 'other', self.owner))
        self.assertEqual(item.expense, expense)
        self.assertEqual(project_budget_summary(self.project)['spent'], Decimal('1850.50'))
        self.act(self.hr, 'purchase_receive', item.pk, actual_cost='999')  # dobara nahi
        self.assertEqual(Expense.objects.count(), 1)
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='has arrived').exists())

    def test_receive_defaults_to_estimate_and_category_mapping(self):
        item = self.approved(project=self.project.pk, category='equipment')
        self.act(self.hr, 'purchase_order', item.pk)
        self.act(self.hr, 'purchase_receive', item.pk)
        expense = Expense.objects.get()
        self.assertEqual((expense.amount, expense.category), (Decimal('2000.00'), 'hardware'))

    def test_receive_without_project_makes_no_expense(self):
        item = self.approved()
        self.act(self.hr, 'purchase_order', item.pk)
        self.act(self.hr, 'purchase_receive', item.pk, actual_cost='1900')
        self.assertEqual(Expense.objects.count(), 0)
        item.refresh_from_db()
        self.assertEqual(item.status, 'received')

    def test_receive_invalid_cost(self):
        item = self.approved(project=self.project.pk)
        self.act(self.hr, 'purchase_order', item.pk)
        for bad in ('abc', '0', '-5', '1e999999'):
            self.act(self.hr, 'purchase_receive', item.pk, actual_cost=bad)
            item.refresh_from_db()
            self.assertEqual(item.status, 'ordered', bad)
        self.assertEqual(Expense.objects.count(), 0)

    def test_budget_alert_on_receive(self):
        small = Project.objects.create(name='Tiny', budget=Decimal('1000'))
        item = self.approved(project=small.pk)
        self.act(self.hr, 'purchase_order', item.pk)
        self.act(self.hr, 'purchase_receive', item.pk, actual_cost='1500')
        self.assertTrue(Notification.objects.filter(message__contains='Budget').exists())

    def test_order_receive_hr_only(self):
        item = self.approved()
        for user in (self.owner, self.manager):
            self.assertEqual(self.act(user, 'purchase_order', item.pk).status_code, 403)
            self.assertEqual(self.act(user, 'purchase_receive', item.pk).status_code, 403)

    def test_cancel_after_order_not_allowed(self):
        item = self.approved()
        self.act(self.hr, 'purchase_order', item.pk)
        self.act(self.owner, 'purchase_cancel', item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'ordered')

    def test_lists_and_queues(self):
        self.ask(title='Mine')
        self.ask(user=self.other, title='Theirs')
        self.login(self.manager)
        queue = [p.title for p in self.client.get(reverse('purchase_list')).context['level1']]
        self.assertEqual(queue, ['Mine'])
        self.login(self.hr)
        queue = sorted(p.title for p in self.client.get(reverse('purchase_list')).context['level1'])
        self.assertEqual(queue, ['Mine', 'Theirs'])
        self.login(self.other)
        page = self.client.get(reverse('purchase_list'))
        self.assertNotContains(page, 'Mine')
        self.assertContains(page, 'Theirs')

    def test_private_project_not_selectable(self):
        secret = Project.objects.create(name='Hidden', is_private=True)
        self.assertEqual(self.ask(project=secret.pk).status_code, 200)
        self.assertEqual(PurchaseRequest.objects.count(), 0)

    def test_dashboard_todos(self):
        self.ask()
        self.login(self.manager)
        self.assertTrue(any('waiting for your approval' in t for t, _ in self.client.get(reverse('dashboard')).context['todo_items']))
        item = PurchaseRequest.objects.get()
        self.act(self.manager, 'purchase_approve', item.pk)
        self.login(self.hr)
        self.assertTrue(any('to order' in t for t, _ in self.client.get(reverse('dashboard')).context['todo_items']))

    def test_vendors(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('vendor_list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('vendor_add')).status_code, 403)
        self.login(self.hr)
        self.client.post(reverse('vendor_add'), {'name': 'Acme', 'is_active': 'on'})
        vendor = Vendor.objects.get()
        self.assertContains(self.client.post(reverse('vendor_add'), {'name': 'Acme'}), 'already exists')
        self.assertEqual(self.client.post(reverse('vendor_add'), {'name': '  '}).status_code, 200)
        self.client.post(reverse('vendor_edit', args=[vendor.pk]), {'name': 'Acme Ltd'})  # is_active chhoota
        vendor.refresh_from_db()
        self.assertEqual((vendor.name, vendor.is_active), ('Acme Ltd', False))
        self.assertEqual(self.client.get(reverse('vendor_list')).status_code, 200)
        self.assertEqual(self.ask(vendor=vendor.pk).status_code, 200)  # inactive vendor chun nahi sakte


class AccessTests(MoneyBase):
    def test_login_required(self):
        for name in ('advance_list', 'advance_add', 'vault_me', 'vault_add', 'purchase_list', 'purchase_add',
                     'vendor_list', 'vault_overview'):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn('/login/', r['Location'])

    def test_pages_open_for_everyone(self):
        for user in (self.owner, self.manager, self.hr, self.admin):
            self.login(user)
            for name in ('advance_list', 'advance_add', 'vault_me', 'vault_add', 'purchase_list', 'purchase_add'):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, (user.username, name))

    def test_sidebar(self):
        self.login(self.owner)
        body = self.client.get(reverse('dashboard')).content.decode()
        for text in ('Salary advance', 'My documents', 'Purchases'):
            self.assertIn(text, body)
        self.assertNotIn('Employee documents', body)
        self.login(self.hr)
        self.assertIn('Employee documents', self.client.get(reverse('dashboard')).content.decode())
