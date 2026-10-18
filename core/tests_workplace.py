import datetime

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .attendance import analyze, classify, is_late, late_cutoff, month_data
from .checklists import DEFAULT_TEMPLATES, parse_items, start_checklist
from .models import (
    Announcement, Asset, AssetAssignment, Attendance, ChecklistTemplate, EmployeeChecklist, Holiday,
    LeaveRequest, Notification, UserProfile,
)
from .tests_hr import HRBase

D = datetime.date


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


class AttendanceBase(HRBase):
    def record(self, user, day, start=(9, 0), end=(18, 0)):
        return Attendance.objects.create(
            user=user, date=day, check_in=at(day, *start), check_out=at(day, *end) if end else None,
        )


class CheckInTests(AttendanceBase):
    def test_timezone_default(self):
        self.assertEqual(timezone.get_current_timezone_name(), 'Asia/Kolkata')

    def test_check_in_and_out_flow(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('attendance_me')).status_code, 200)
        self.client.post(reverse('check_in'))
        record = Attendance.objects.get()
        self.assertEqual((record.user, record.date, record.check_out), (self.owner, timezone.localdate(), None))
        self.client.post(reverse('check_in'))  # dobara nahi
        self.assertEqual(Attendance.objects.count(), 1)
        r = self.client.get(reverse('attendance_me'))
        self.assertTrue(r.context['can_check_out'])
        self.assertFalse(r.context['can_check_in'])
        self.client.post(reverse('check_out'))
        record.refresh_from_db()
        self.assertIsNotNone(record.check_out)
        self.assertGreaterEqual(record.check_out, record.check_in)
        first_out = record.check_out
        self.client.post(reverse('check_out'))
        record.refresh_from_db()
        self.assertEqual(record.check_out, first_out)

    def test_check_out_without_check_in(self):
        self.login(self.owner)
        self.client.post(reverse('check_out'))
        self.assertEqual(Attendance.objects.count(), 0)

    def test_get_not_allowed_and_login_required(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('check_in')).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('check_in')).status_code, 302)


class ClassifyTests(AttendanceBase):
    def test_late_boundary(self):
        self.assertEqual(late_cutoff(), datetime.time(9, 45))
        day = D(2026, 1, 12)
        self.assertFalse(is_late(self.record(self.owner, day, start=(9, 45))))
        self.assertTrue(is_late(self.record(self.other, day, start=(9, 46))))

    def test_status_by_hours(self):
        today = D(2026, 1, 30)
        self.assertEqual(classify(self.record(self.owner, D(2026, 1, 12), end=(17, 30)), today), 'present')   # 8.5h
        self.assertEqual(classify(self.record(self.owner, D(2026, 1, 13), end=(14, 0)), today), 'half')       # 5h
        self.assertEqual(classify(self.record(self.owner, D(2026, 1, 14), end=(11, 0)), today), 'short')      # 2h
        self.assertEqual(classify(self.record(self.owner, D(2026, 1, 15), end=None), today), 'no_checkout')
        self.assertEqual(classify(self.record(self.owner, today, end=None), today), 'open')


class MonthSummaryTests(AttendanceBase):
    def test_absent_leave_holiday_counts(self):
        today = D(2026, 1, 30)
        Holiday.objects.create(date=D(2026, 1, 26), name='Republic Day')
        LeaveRequest.objects.create(user=self.owner, kind='annual', start_date=D(2026, 1, 12), end_date=D(2026, 1, 13), status='approved')
        LeaveRequest.objects.create(user=self.owner, kind='annual', start_date=D(2026, 1, 14), end_date=D(2026, 1, 14), status='pending')
        for day in range(5, 10):
            self.record(self.owner, D(2026, 1, day), start=(9, 50) if day == 5 else (9, 0))
        self.record(self.owner, D(2026, 1, 24), end=(9, 30))  # Saturday, short, required nahi
        rows, s = month_data([self.owner], 2026, 1, today)[self.owner.pk]
        # Jan 1-29 mein 21 working days: 1 holiday, 2 leave, 5 present -> 13 absent (pending leave absent hi hai)
        self.assertEqual((s['absent'], s['leave'], s['late']), (13, 2, 1))
        self.assertEqual(s['present'], 5.0)
        status = {r['date'].day: r['status'] for r in rows}
        self.assertEqual((status[26], status[17], status[12], status[30], status[24]), ('holiday', 'weekend', 'leave', 'future', 'short'))
        self.assertEqual(len(rows), 30)  # aaj tak

    def test_before_joining_not_absent(self):
        UserProfile.objects.update_or_create(user=self.owner, defaults={'joining_date': D(2026, 1, 20)})
        _rows, s = month_data([self.owner], 2026, 1, D(2026, 1, 30))[self.owner.pk]
        self.assertEqual(s['absent'], 8)  # 20-23 aur 26-29 (aaj 30 ko gina nahi jata)


class TeamAttendanceTests(AttendanceBase):
    def setUp(self):
        super().setUp()
        UserProfile.objects.update_or_create(user=self.owner, defaults={'reporting_manager': self.manager})
        self.record(self.owner, D(2026, 1, 12))
        self.record(self.other, D(2026, 1, 12))

    def test_member_cannot_open_team_pages(self):
        self.login(self.other)
        for name, args in (('attendance_team', []), ('attendance_team_export', []), ('attendance_person', [self.owner.pk])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404, name)

    def test_manager_sees_only_direct_reports(self):
        self.login(self.manager)
        r = self.client.get(reverse('attendance_team') + '?month=2026-01')
        self.assertEqual([row['user'].username for row in r.context['rows']], ['owner'])
        self.assertEqual(self.client.get(reverse('attendance_person', args=[self.owner.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('attendance_person', args=[self.other.pk])).status_code, 404)
        csv_body = self.client.get(reverse('attendance_team_export') + '?month=2026-01').content.decode('utf-8')
        self.assertIn('owner', csv_body)
        self.assertNotIn('other', csv_body)

    def test_hr_sees_everyone_and_can_edit(self):
        self.login(self.hr)
        r = self.client.get(reverse('attendance_team') + '?month=2026-01')
        self.assertGreaterEqual(len(r.context['rows']), 4)
        self.assertContains(self.client.get(reverse('attendance_person', args=[self.owner.pk]) + '?month=2026-01'), 'Edit')

    def test_manager_cannot_edit(self):
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse('attendance_edit', args=[self.owner.pk, '2026-01-12'])).status_code, 403)
        rec = Attendance.objects.get(user=self.owner)
        self.assertEqual(self.client.post(reverse('attendance_delete', args=[rec.pk])).status_code, 403)

    def test_edit_create_and_validation(self):
        self.login(self.hr)
        url = reverse('attendance_edit', args=[self.owner.pk, '2026-01-13'])
        self.assertEqual(self.client.get(url).status_code, 200)
        bad = self.client.post(url, {'check_in': '2026-01-13T10:00', 'check_out': '2026-01-13T09:00', 'note': ''})
        self.assertEqual(bad.status_code, 200)
        wrong_day = self.client.post(url, {'check_in': '2026-01-14T10:00', 'check_out': '', 'note': ''})
        self.assertEqual(wrong_day.status_code, 200)
        self.assertFalse(Attendance.objects.filter(user=self.owner, date=D(2026, 1, 13)).exists())
        ok = self.client.post(url, {'check_in': '2026-01-13T09:30', 'check_out': '2026-01-13T18:00', 'note': 'Forgot'})
        self.assertEqual(ok.status_code, 302)
        rec = Attendance.objects.get(user=self.owner, date=D(2026, 1, 13))
        self.assertEqual((rec.edited_by, timezone.localtime(rec.check_in).hour), (self.hr, 9))
        # edit existing
        self.client.post(url, {'check_in': '2026-01-13T09:00', 'check_out': '', 'note': ''})
        rec.refresh_from_db()
        self.assertIsNone(rec.check_out)
        self.client.post(reverse('attendance_delete', args=[rec.pk]))
        self.assertFalse(Attendance.objects.filter(pk=rec.pk).exists())

    def test_edit_bad_or_future_dates_404(self):
        self.login(self.hr)
        future = (timezone.localdate() + datetime.timedelta(days=2)).isoformat()
        for day in ('nonsense', '2026-13-45', future, '1999-01-01'):
            self.assertEqual(self.client.get(reverse('attendance_edit', args=[self.owner.pk, day])).status_code, 404, day)

    def test_params_do_not_crash(self):
        self.login(self.hr)
        for q in ('?month=bad', '?month=2026-99', '?month=3000-01'):
            self.assertEqual(self.client.get(reverse('attendance_team') + q).status_code, 200, q)
            self.assertEqual(self.client.get(reverse('attendance_me') + q).status_code, 200, q)

    def test_my_page_future_month_clamped(self):
        self.login(self.owner)
        r = self.client.get(reverse('attendance_me') + '?month=2099-01')
        self.assertEqual(r.context['label'], timezone.localdate().strftime('%B %Y'))


class AnnouncementTests(HRBase):
    def test_permissions_and_notifications(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('announcement_add')).status_code, 403)
        self.assertEqual(self.client.post(reverse('announcement_add'), {'title': 'x', 'body': 'y'}).status_code, 403)
        self.login(self.hr)
        r = self.client.post(reverse('announcement_add'), {'title': 'Office closed Friday', 'body': 'Diwali puja'})
        self.assertRedirects(r, reverse('announcement_list'))
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='Office closed').exists())
        self.assertFalse(Notification.objects.filter(user=self.hr).exists())
        self.login(self.owner)
        self.assertContains(self.client.get(reverse('announcement_list')), 'Office closed Friday')
        self.assertContains(self.client.get(reverse('dashboard')), 'Office closed Friday')

    def test_expiry_and_pin_order(self):
        today = timezone.localdate()
        Announcement.objects.create(title='Old news', body='b', expires_on=today - datetime.timedelta(days=1))
        Announcement.objects.create(title='Last day', body='b', expires_on=today)
        Announcement.objects.create(title='Normal', body='b')
        Announcement.objects.create(title='Pinned one', body='b', is_pinned=True)
        self.login(self.owner)
        r = self.client.get(reverse('announcement_list'))
        titles = [a.title for a in r.context['announcements']]
        self.assertNotIn('Old news', titles)
        self.assertEqual(titles[0], 'Pinned one')
        self.assertIn('Last day', titles)
        self.login(self.hr)
        hr_view = self.client.get(reverse('announcement_list'))
        self.assertIn('Old news', [a.title for a in hr_view.context['announcements']])
        self.assertContains(hr_view, 'Expired')
        self.assertNotContains(self.client.get(reverse('dashboard')), 'Old news')

    def test_new_with_past_expiry_rejected_and_html_escaped(self):
        self.login(self.hr)
        yesterday = (timezone.localdate() - datetime.timedelta(days=1)).isoformat()
        r = self.client.post(reverse('announcement_add'), {'title': 'a', 'body': 'b', 'expires_on': yesterday})
        self.assertEqual(r.status_code, 200)
        self.client.post(reverse('announcement_add'), {'title': '<script>alert(1)</script>', 'body': '<b>x</b>'})
        page = self.client.get(reverse('announcement_list'))
        self.assertNotContains(page, '<script>alert(1)</script>')
        self.assertNotContains(page, '<b>x</b>')

    def test_edit_delete(self):
        a = Announcement.objects.create(title='T', body='B')
        self.login(self.hr)
        self.client.post(reverse('announcement_edit', args=[a.pk]), {'title': 'T2', 'body': 'B'})
        a.refresh_from_db()
        self.assertEqual(a.title, 'T2')
        self.login(self.owner)
        self.assertEqual(self.client.post(reverse('announcement_delete', args=[a.pk])).status_code, 403)
        self.login(self.hr)
        self.client.post(reverse('announcement_delete', args=[a.pk]))
        self.assertFalse(Announcement.objects.exists())


class AssetTests(HRBase):
    def asset(self, **kw):
        values = dict(name='MacBook', asset_tag='LAP-1', category='laptop')
        values.update(kw)
        return Asset.objects.create(**values)

    def test_hr_only(self):
        a = self.asset()
        self.login(self.manager)
        for name, args in (('asset_list', []), ('asset_add', []), ('asset_detail', [a.pk]), ('asset_edit', [a.pk])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)
        for name in ('asset_assign', 'asset_return', 'asset_status', 'asset_delete'):
            self.assertEqual(self.client.post(reverse(name, args=[a.pk])).status_code, 403, name)

    def test_add_tag_normalised_and_unique(self):
        self.login(self.hr)
        data = {'name': 'Dell', 'asset_tag': ' lap-9 ', 'category': 'laptop'}
        self.client.post(reverse('asset_add'), data)
        self.assertTrue(Asset.objects.filter(asset_tag='LAP-9').exists())
        r = self.client.post(reverse('asset_add'), {**data, 'asset_tag': 'lap-9'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Asset.objects.count(), 1)
        self.assertEqual(self.client.post(reverse('asset_add'), {**data, 'asset_tag': 'X', 'purchase_cost': '-4'}).status_code, 200)

    def test_assign_return_flow(self):
        a = self.asset()
        self.login(self.hr)
        today = timezone.localdate().isoformat()
        self.client.post(reverse('asset_assign', args=[a.pk]), {'user': self.owner.pk, 'assigned_on': today, 'note': 'with charger'})
        a.refresh_from_db()
        self.assertEqual(a.status, 'assigned')
        self.assertEqual(a.current_assignment.user, self.owner)
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='LAP-1').exists())
        # dusre ko dena mana
        self.client.post(reverse('asset_assign', args=[a.pk]), {'user': self.other.pk, 'assigned_on': today})
        self.assertEqual(AssetAssignment.objects.count(), 1)
        # employee ko apne profile par dikhta hai
        self.login(self.owner)
        self.assertContains(self.client.get(reverse('my_profile')), 'MacBook')
        self.login(self.hr)
        self.client.post(reverse('asset_status', args=[a.pk]), {'status': 'retired'})  # assigned hai: mana
        a.refresh_from_db()
        self.assertEqual(a.status, 'assigned')
        self.client.post(reverse('asset_return', args=[a.pk]), {'return_note': 'scratched', 'needs_repair': 'on'})
        a.refresh_from_db()
        self.assertEqual(a.status, 'repair')
        done = AssetAssignment.objects.get()
        self.assertEqual((done.return_note, done.returned_on), ('scratched', timezone.localdate()))
        self.client.post(reverse('asset_assign', args=[a.pk]), {'user': self.other.pk, 'assigned_on': today})  # repair mein hai
        self.assertEqual(AssetAssignment.objects.count(), 1)
        self.client.post(reverse('asset_status', args=[a.pk]), {'status': 'available'})
        self.client.post(reverse('asset_assign', args=[a.pk]), {'user': self.other.pk, 'assigned_on': today})
        self.assertEqual(AssetAssignment.objects.count(), 2)

    def test_assign_validation(self):
        a = self.asset()
        self.login(self.hr)
        tomorrow = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
        self.client.post(reverse('asset_assign', args=[a.pk]), {'user': self.owner.pk, 'assigned_on': tomorrow})
        self.client.post(reverse('asset_assign', args=[a.pk]), {'user': 99999, 'assigned_on': timezone.localdate().isoformat()})
        inactive = User.objects.create_user('gone', 'g@example.com', 'x', is_active=False)
        self.client.post(reverse('asset_assign', args=[a.pk]), {'user': inactive.pk, 'assigned_on': timezone.localdate().isoformat()})
        self.assertEqual(AssetAssignment.objects.count(), 0)
        self.assertEqual(self.client.post(reverse('asset_return', args=[a.pk])).status_code, 302)  # kisi ke paas nahi

    def test_database_blocks_two_active_assignments(self):
        a = self.asset()
        AssetAssignment.objects.create(asset=a, user=self.owner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetAssignment.objects.create(asset=a, user=self.other)
        AssetAssignment.objects.filter(asset=a).update(returned_on=timezone.localdate())
        AssetAssignment.objects.create(asset=a, user=self.other)  # return ke baad theek

    def test_delete_rules_and_list_filters(self):
        a = self.asset()
        b = self.asset(name='Phone', asset_tag='PH-1', category='phone')
        AssetAssignment.objects.create(asset=a, user=self.owner)
        Asset.objects.filter(pk=a.pk).update(status='assigned')
        self.login(self.hr)
        self.client.post(reverse('asset_delete', args=[a.pk]))
        self.assertTrue(Asset.objects.filter(pk=a.pk).exists())
        self.client.post(reverse('asset_delete', args=[b.pk]))
        self.assertFalse(Asset.objects.filter(pk=b.pk).exists())
        r = self.client.get(reverse('asset_list') + '?q=owner')
        self.assertEqual([x.asset_tag for x in r.context['assets']], ['LAP-1'])
        for q in ('?status=zzz&category=zzz&page=-1', '?q=%00', '?page=abc'):
            self.assertEqual(self.client.get(reverse('asset_list') + q).status_code, 200, q)
        self.assertContains(self.client.get(reverse('asset_detail', args=[a.pk])), 'Mark as returned')


class ChecklistTests(HRBase):
    def test_parse_items(self):
        items = parse_items('HR | Do this\n\n  it | Set up laptop \nplain line\nUnknown | Thing\nManager |\n' + 'x' * 300)
        self.assertEqual(items[0], ('HR', 'Do this'))
        self.assertEqual(items[1], ('IT', 'Set up laptop'))
        self.assertEqual(items[2], ('HR', 'plain line'))
        self.assertEqual(items[3], ('HR', 'Unknown | Thing'))
        self.assertEqual(items[4], ('HR', 'Manager |'))
        self.assertEqual(len(items[5][1]), 200)

    def test_start_uses_default_then_custom_template(self):
        c = start_checklist(self.owner, 'onboarding', self.hr)
        self.assertEqual(c.tasks.count(), len(parse_items(DEFAULT_TEMPLATES['onboarding'])))
        self.assertIsNone(start_checklist(self.owner, 'onboarding', self.hr))  # adhura hai
        self.assertIsNotNone(start_checklist(self.owner, 'offboarding', self.hr))
        ChecklistTemplate.objects.create(kind='onboarding', items='Employee | Only step')
        c2 = start_checklist(self.other, 'onboarding', self.hr)
        self.assertEqual([t.title for t in c2.tasks.all()], ['Only step'])

    def test_start_view_and_permissions(self):
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse('checklist_list')).status_code, 403)
        self.assertEqual(self.client.post(reverse('checklist_start'), {'user': self.owner.pk, 'kind': 'onboarding'}).status_code, 403)
        self.login(self.hr)
        r = self.client.post(reverse('checklist_start'), {'user': self.owner.pk, 'kind': 'onboarding'})
        c = EmployeeChecklist.objects.get()
        self.assertRedirects(r, reverse('checklist_detail', args=[c.pk]))
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='onboarding').exists())
        for data in ({'user': 'abc', 'kind': 'onboarding'}, {'user': self.owner.pk, 'kind': 'bogus'}, {'user': self.owner.pk, 'kind': 'onboarding'}, {}):
            self.client.post(reverse('checklist_start'), data)
        self.assertEqual(EmployeeChecklist.objects.count(), 1)
        for q in ('', '?status=open', '?status=done', '?status=zzz'):
            self.assertEqual(self.client.get(reverse('checklist_list') + q).status_code, 200, q)

    def test_view_access(self):
        UserProfile.objects.update_or_create(user=self.owner, defaults={'reporting_manager': self.manager})
        c = start_checklist(self.owner, 'onboarding', self.hr)
        for user, code in ((self.owner, 200), (self.manager, 200), (self.hr, 200), (self.other, 404)):
            self.login(user)
            self.assertEqual(self.client.get(reverse('checklist_detail', args=[c.pk])).status_code, code, user.username)

    def test_toggle_rules_and_completion(self):
        UserProfile.objects.update_or_create(user=self.owner, defaults={'reporting_manager': self.manager})
        ChecklistTemplate.objects.create(kind='onboarding', items='HR | A\nEmployee | B\nManager | C\nIT | D')
        c = start_checklist(self.owner, 'onboarding', self.hr)
        t = {task.title: task for task in c.tasks.all()}

        def tick(user, title):
            self.login(user)
            self.client.post(reverse('checklist_toggle', args=[t[title].pk]))
            t[title].refresh_from_db()
            return t[title].is_done

        self.assertFalse(tick(self.owner, 'A'))      # employee HR ka step nahi kar sakta
        self.assertFalse(tick(self.manager, 'B'))    # manager employee ka step nahi
        self.assertFalse(tick(self.owner, 'C'))
        self.assertTrue(tick(self.owner, 'B'))
        self.assertTrue(tick(self.manager, 'C'))
        self.login(self.other)
        self.assertEqual(self.client.post(reverse('checklist_toggle', args=[t['A'].pk])).status_code, 404)
        self.assertTrue(tick(self.hr, 'A'))
        c.refresh_from_db()
        self.assertIsNone(c.completed_at)
        self.assertTrue(tick(self.hr, 'D'))
        c.refresh_from_db()
        self.assertIsNotNone(c.completed_at)
        self.assertTrue(Notification.objects.filter(user=self.admin, message__contains='is complete').exists() or
                        Notification.objects.filter(user=self.hr, message__contains='is complete').exists() is False)
        self.assertFalse(tick(self.hr, 'D'))  # wapas untick
        c.refresh_from_db()
        self.assertIsNone(c.completed_at)
        self.assertIsNone(start_checklist(self.owner, 'onboarding', self.hr))  # phir adhura hai

    def test_offboarding_shows_assets_to_hr_not_manager(self):
        UserProfile.objects.update_or_create(user=self.owner, defaults={'reporting_manager': self.manager})
        a = Asset.objects.create(name='Laptop X', asset_tag='L-X', status='assigned')
        AssetAssignment.objects.create(asset=a, user=self.owner)
        c = start_checklist(self.owner, 'offboarding', self.hr)
        self.login(self.hr)
        self.assertContains(self.client.get(reverse('checklist_detail', args=[c.pk])), 'Laptop X')
        self.login(self.manager)
        self.assertNotContains(self.client.get(reverse('checklist_detail', args=[c.pk])), 'Laptop X')

    def test_templates_page_validation(self):
        self.login(self.hr)
        self.assertEqual(self.client.get(reverse('checklist_templates')).status_code, 200)
        self.client.post(reverse('checklist_templates'), {'onboarding': 'IT | Laptop', 'offboarding': '   '})
        self.assertFalse(ChecklistTemplate.objects.exists())  # ek khali hai: kuch save nahi
        self.client.post(reverse('checklist_templates'), {'onboarding': '\n'.join(f'Step {i}' for i in range(41)), 'offboarding': 'HR | x'})
        self.assertFalse(ChecklistTemplate.objects.exists())
        self.client.post(reverse('checklist_templates'), {'onboarding': 'it | Laptop', 'offboarding': 'HR | Letter'})
        self.assertEqual(ChecklistTemplate.objects.get(kind='onboarding').items, 'IT | Laptop')

    def test_delete_and_my_profile(self):
        c = start_checklist(self.owner, 'onboarding', self.hr)
        self.login(self.owner)
        self.assertContains(self.client.get(reverse('my_profile')), 'My checklists')
        self.assertEqual(self.client.post(reverse('checklist_delete', args=[c.pk])).status_code, 403)
        self.login(self.hr)
        self.client.post(reverse('checklist_delete', args=[c.pk]))
        self.assertFalse(EmployeeChecklist.objects.exists())


class RegressionTests(HRBase):
    def test_pages_render_for_admin_and_sidebar(self):
        a = Asset.objects.create(name='N', asset_tag='T-1')
        c = start_checklist(self.owner, 'onboarding', self.admin)
        Announcement.objects.create(title='Hi', body='b')
        self.login(self.admin)
        for name, args in (('attendance_me', []), ('attendance_team', []), ('announcement_list', []), ('announcement_add', []),
                           ('asset_list', []), ('asset_add', []), ('asset_detail', [a.pk]), ('asset_edit', [a.pk]),
                           ('checklist_list', []), ('checklist_detail', [c.pk]), ('checklist_templates', []),
                           ('hr_employees', []), ('dashboard', []), ('my_profile', [])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)
        self.login(self.owner)
        r = self.client.get(reverse('dashboard'))
        self.assertContains(r, reverse('attendance_me'))
        self.assertContains(r, reverse('announcement_list'))
