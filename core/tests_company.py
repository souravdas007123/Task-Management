import datetime

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Candidate, CompanyDocument, DocumentAck, JobOpening, Notification, Review, ReviewCycle, Room, RoomBooking,
    Ticket, UserProfile, Visitor,
)
from .tests_hr import HRBase

TMP_MEDIA = override_settings(MEDIA_ROOT='/tmp/office_hub_test_media')


def tomorrow_at(hour, minute=0, days=1):
    day = timezone.localdate() + datetime.timedelta(days=days)
    return day, datetime.time(hour, minute)


class SmokeAndAccess(HRBase):
    open_urls = ['room_list', 'room_book', 'visitor_list', 'visitor_add', 'document_list', 'ticket_list',
                 'ticket_add', 'review_home']
    hr_urls = ['room_add', 'document_add', 'job_list', 'job_add', 'cycle_add']

    def test_everyone_can_open_common_pages(self):
        for user in (self.owner, self.manager, self.hr, self.admin):
            self.login(user)
            for name in self.open_urls:
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, (user.username, name))

    def test_hr_only_pages(self):
        for user in (self.owner, self.manager):
            self.login(user)
            for name in self.hr_urls:
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, (user.username, name))
        for user in (self.hr, self.admin):
            self.login(user)
            for name in self.hr_urls:
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, (user.username, name))

    def test_login_required(self):
        for name in self.open_urls + self.hr_urls:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn('/login/', r['Location'])

    def test_sidebar_sections(self):
        self.login(self.owner)
        body = self.client.get(reverse('dashboard')).content.decode()
        for text in ('Meeting rooms', 'Visitors', 'Helpdesk', 'Documents', 'Reviews'):
            self.assertIn(text, body)
        self.assertNotIn('Recruitment', body)
        self.login(self.hr)
        self.assertIn('Recruitment', self.client.get(reverse('dashboard')).content.decode())


class RoomTests(HRBase):
    def setUp(self):
        super().setUp()
        self.room = Room.objects.create(name='Boardroom', capacity=10)

    def book(self, user, room=None, start=(10, 0), end=(11, 0), days=1, title='Standup'):
        self.login(user)
        day, _ = tomorrow_at(0, days=days)
        return self.client.post(reverse('room_book'), {
            'room': (room or self.room).pk, 'title': title, 'day': day.isoformat(),
            'start_time': '%02d:%02d' % start, 'end_time': '%02d:%02d' % end,
        })

    def test_book_and_list(self):
        r = self.book(self.owner)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(RoomBooking.objects.count(), 1)
        day, _ = tomorrow_at(0)
        page = self.client.get(reverse('room_list'), {'date': day.isoformat()})
        self.assertContains(page, 'Standup')
        self.assertContains(page, '10:00')

    def test_overlap_rejected_but_adjacent_allowed(self):
        self.book(self.owner, start=(10, 0), end=(11, 0))
        r = self.book(self.other, start=(10, 30), end=(11, 30))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'already booked')
        self.assertEqual(RoomBooking.objects.count(), 1)
        self.book(self.other, start=(11, 0), end=(12, 0))
        self.book(self.other, start=(9, 0), end=(10, 0))
        self.assertEqual(RoomBooking.objects.count(), 3)
        # Doosre room mein same time theek hai
        room2 = Room.objects.create(name='Huddle', capacity=4)
        self.book(self.other, room=room2, start=(10, 0), end=(11, 0))
        self.assertEqual(RoomBooking.objects.count(), 4)

    def test_validation(self):
        self.assertEqual(self.book(self.owner, start=(11, 0), end=(10, 0)).status_code, 200)
        self.assertEqual(self.book(self.owner, start=(10, 0), end=(10, 0)).status_code, 200)
        self.assertEqual(self.book(self.owner, days=-1).status_code, 200)  # beeta hua din
        self.assertEqual(self.book(self.owner, start=(0, 0), end=(23, 59)).status_code, 200)  # 12 ghante se lamba
        self.assertEqual(RoomBooking.objects.count(), 0)
        self.assertEqual(self.book(self.owner, title='   ').status_code, 200)
        self.assertEqual(RoomBooking.objects.count(), 0)

    def test_inactive_room_not_bookable(self):
        self.room.is_active = False
        self.room.save()
        r = self.book(self.owner)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(RoomBooking.objects.count(), 0)
        self.login(self.owner)
        self.assertNotContains(self.client.get(reverse('room_list')), 'Boardroom')
        self.login(self.hr)
        self.assertContains(self.client.get(reverse('room_list')), 'Boardroom')

    def test_cancel_permissions(self):
        self.book(self.owner)
        booking = RoomBooking.objects.get()
        self.login(self.other)
        self.client.post(reverse('booking_cancel', args=[booking.pk]))
        self.assertEqual(RoomBooking.objects.count(), 1)
        self.login(self.owner)
        self.client.post(reverse('booking_cancel', args=[booking.pk]))
        self.assertEqual(RoomBooking.objects.count(), 0)
        self.book(self.owner)
        self.login(self.hr)
        self.client.post(reverse('booking_cancel', args=[RoomBooking.objects.get().pk]))
        self.assertEqual(RoomBooking.objects.count(), 0)
        self.assertEqual(self.client.get(reverse('booking_cancel', args=[1])).status_code, 405)

    def test_bad_inputs_do_not_crash(self):
        self.login(self.owner)
        for q in ({'date': 'garbage'}, {'date': '2026-13-45'}, {'date': ''}):
            self.assertEqual(self.client.get(reverse('room_list'), q).status_code, 200)
        self.assertEqual(self.client.get(reverse('room_book'), {'room': 'zz', 'date': 'x'}).status_code, 200)
        self.assertEqual(self.client.post(reverse('room_book'), {}).status_code, 200)

    def test_hr_can_add_and_edit_rooms(self):
        self.login(self.hr)
        self.client.post(reverse('room_add'), {'name': 'Huddle', 'capacity': 4, 'location': '', 'amenities': '', 'is_active': 'on'})
        self.assertTrue(Room.objects.filter(name='Huddle').exists())
        r = self.client.post(reverse('room_add'), {'name': 'Huddle', 'capacity': 4})
        self.assertContains(r, 'already exists')
        self.assertEqual(self.client.post(reverse('room_add'), {'name': 'X', 'capacity': 0}).status_code, 200)
        self.client.post(reverse('room_edit', args=[self.room.pk]), {'name': 'Boardroom', 'capacity': 12})
        self.room.refresh_from_db()
        self.assertEqual((self.room.capacity, self.room.is_active), (12, False))


class VisitorTests(HRBase):
    def register(self, user, **extra):
        self.login(user)
        data = {'name': 'Asha Roy', 'company': 'Acme', 'phone': '999', 'purpose': 'Meeting',
                'expected_on': timezone.localdate().isoformat()}
        data.update(extra)
        return self.client.post(reverse('visitor_add'), data)

    def test_employee_registers_for_self_only(self):
        self.register(self.owner, host=self.other.pk)
        visitor = Visitor.objects.get()
        self.assertEqual((visitor.host, visitor.registered_by), (self.owner, self.owner))

    def test_hr_can_choose_host_and_host_is_notified(self):
        self.register(self.hr, host=self.owner.pk)
        visitor = Visitor.objects.get()
        self.assertEqual(visitor.host, self.owner)
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='Asha Roy').exists())

    def test_past_date_and_blank_name_rejected(self):
        past = (timezone.localdate() - datetime.timedelta(days=1)).isoformat()
        self.assertEqual(self.register(self.owner, expected_on=past).status_code, 200)
        self.assertEqual(self.register(self.owner, name='  ').status_code, 200)
        self.assertEqual(Visitor.objects.count(), 0)

    def test_visibility(self):
        self.register(self.owner)
        self.register(self.other, name='Second Guest')
        self.login(self.owner)
        page = self.client.get(reverse('visitor_list'))
        self.assertContains(page, 'Asha Roy')
        self.assertNotContains(page, 'Second Guest')
        self.login(self.hr)
        page = self.client.get(reverse('visitor_list'))
        self.assertContains(page, 'Asha Roy')
        self.assertContains(page, 'Second Guest')

    def test_check_in_out_flow(self):
        self.register(self.owner)
        visitor = Visitor.objects.get()
        self.login(self.owner)  # employee reception nahi hai
        self.assertEqual(self.client.post(reverse('visitor_checkin', args=[visitor.pk])).status_code, 403)
        self.login(self.hr)
        self.client.post(reverse('visitor_checkout', args=[visitor.pk]))  # abhi andar nahi
        visitor.refresh_from_db()
        self.assertEqual(visitor.status, 'expected')
        self.client.post(reverse('visitor_checkin', args=[visitor.pk]), {'badge_no': 'B-7'})
        visitor.refresh_from_db()
        self.assertEqual((visitor.status, visitor.badge_no), ('in', 'B-7'))
        self.assertIsNotNone(visitor.checked_in_at)
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='has arrived').exists())
        self.assertEqual(self.client.get(reverse('visitor_list')).context['inside'], 1)
        self.client.post(reverse('visitor_checkin', args=[visitor.pk]))  # dobara nahi
        self.client.post(reverse('visitor_checkout', args=[visitor.pk]))
        visitor.refresh_from_db()
        self.assertEqual(visitor.status, 'out')
        self.assertIsNotNone(visitor.checked_out_at)

    def test_cancel_rules(self):
        self.register(self.owner)
        visitor = Visitor.objects.get()
        self.login(self.other)
        self.assertEqual(self.client.post(reverse('visitor_cancel', args=[visitor.pk])).status_code, 404)
        self.login(self.owner)
        self.client.post(reverse('visitor_cancel', args=[visitor.pk]))
        visitor.refresh_from_db()
        self.assertEqual(visitor.status, 'cancelled')
        self.login(self.hr)
        self.client.post(reverse('visitor_checkin', args=[visitor.pk]))
        visitor.refresh_from_db()
        self.assertEqual(visitor.status, 'cancelled')

    def test_filters_do_not_crash(self):
        self.register(self.owner)
        self.login(self.hr)
        for q in ({'status': 'zzz'}, {'q': 'asha'}, {'page': 'x'}, {'status': 'expected', 'q': "'; drop"}):
            self.assertEqual(self.client.get(reverse('visitor_list'), q).status_code, 200)
        self.assertContains(self.client.get(reverse('visitor_list'), {'q': 'acme'}), 'Asha Roy')


@TMP_MEDIA
class DocumentTests(HRBase):
    def upload(self, name='policy.pdf', **extra):
        self.login(self.hr)
        data = {'title': 'Leave policy', 'category': 'policy', 'description': '', 'requires_ack': 'on',
                'file': SimpleUploadedFile(name, b'%PDF-1.4 test')}
        data.update(extra)
        return self.client.post(reverse('document_add'), data)

    def test_upload_and_notify(self):
        self.upload()
        doc = CompanyDocument.objects.get()
        self.assertEqual((doc.version, doc.uploaded_by), (1, self.hr))
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='acknowledge').exists())

    def test_bad_extension_rejected(self):
        r = self.upload(name='virus.exe')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.upload(name='page.html').status_code, 200)
        self.assertEqual(CompanyDocument.objects.count(), 0)

    def test_employee_cannot_upload_or_delete(self):
        self.upload()
        doc = CompanyDocument.objects.get()
        self.login(self.owner)
        self.assertEqual(self.client.post(reverse('document_add'), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse('document_delete', args=[doc.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('document_acks', args=[doc.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('document_edit', args=[doc.pk])).status_code, 403)

    def test_acknowledge_once_and_pending_count(self):
        self.upload()
        doc = CompanyDocument.objects.get()
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('document_list')).context['pending'], 1)
        self.client.post(reverse('document_ack', args=[doc.pk]))
        self.client.post(reverse('document_ack', args=[doc.pk]))
        self.assertEqual(DocumentAck.objects.filter(user=self.owner).count(), 1)
        self.assertEqual(self.client.get(reverse('document_list')).context['pending'], 0)

    def test_new_version_needs_new_ack(self):
        self.upload()
        doc = CompanyDocument.objects.get()
        self.login(self.owner)
        self.client.post(reverse('document_ack', args=[doc.pk]))
        self.login(self.hr)
        self.client.post(reverse('document_edit', args=[doc.pk]), {
            'title': 'Leave policy', 'category': 'policy', 'requires_ack': 'on',
            'file': SimpleUploadedFile('policy2.pdf', b'%PDF-1.4 v2'),
        })
        doc.refresh_from_db()
        self.assertEqual(doc.version, 2)
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('document_list')).context['pending'], 1)
        # Sirf title badalne se version nahi badhta
        self.login(self.hr)
        self.client.post(reverse('document_edit', args=[doc.pk]), {'title': 'Leave policy 2026', 'category': 'policy', 'requires_ack': 'on'})
        doc.refresh_from_db()
        self.assertEqual((doc.version, doc.title), (2, 'Leave policy 2026'))

    def test_ack_report(self):
        self.upload()
        doc = CompanyDocument.objects.get()
        self.login(self.owner)
        self.client.post(reverse('document_ack', args=[doc.pk]))
        self.login(self.hr)
        r = self.client.get(reverse('document_acks', args=[doc.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['done'], 1)
        self.assertEqual(r.context['total'], User.objects.filter(is_active=True).count())

    def test_not_required_doc_has_no_pending(self):
        self.upload(requires_ack='')
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('document_list')).context['pending'], 0)

    def test_delete(self):
        self.upload()
        doc = CompanyDocument.objects.get()
        self.client.post(reverse('document_delete', args=[doc.pk]))
        self.assertEqual(CompanyDocument.objects.count(), 0)


class TicketTests(HRBase):
    def raise_ticket(self, user, **extra):
        self.login(user)
        data = {'category': 'it', 'priority': 'high', 'subject': 'Laptop broken', 'description': 'Screen is black'}
        data.update(extra)
        return self.client.post(reverse('ticket_add'), data)

    def test_raise_notifies_hr(self):
        r = self.raise_ticket(self.owner)
        ticket = Ticket.objects.get()
        self.assertRedirects(r, reverse('ticket_detail', args=[ticket.pk]))
        self.assertEqual(ticket.raised_by, self.owner)
        self.assertTrue(Notification.objects.filter(user=self.hr, message__contains='Laptop broken').exists())
        self.assertFalse(Notification.objects.filter(user=self.owner).exists())

    def test_blank_fields_rejected(self):
        self.assertEqual(self.raise_ticket(self.owner, subject='  ').status_code, 200)
        self.assertEqual(self.raise_ticket(self.owner, description=' ').status_code, 200)
        self.assertEqual(self.raise_ticket(self.owner, category='nope').status_code, 200)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_visibility(self):
        self.raise_ticket(self.owner)
        ticket = Ticket.objects.get()
        self.login(self.other)
        self.assertEqual(self.client.get(reverse('ticket_detail', args=[ticket.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('ticket_list')), 'Laptop broken')
        self.assertEqual(self.client.post(reverse('ticket_comment', args=[ticket.pk]), {'body': 'hi'}).status_code, 404)
        self.login(self.manager)  # manager bhi nahi dekh sakta
        self.assertEqual(self.client.get(reverse('ticket_detail', args=[ticket.pk])).status_code, 404)
        for user in (self.owner, self.hr, self.admin):
            self.login(user)
            self.assertEqual(self.client.get(reverse('ticket_detail', args=[ticket.pk])).status_code, 200)

    def test_assignee_can_see(self):
        self.raise_ticket(self.owner)
        ticket = Ticket.objects.get()
        self.login(self.hr)
        self.client.post(reverse('ticket_manage', args=[ticket.pk]), {'status': 'in_progress', 'priority': 'high', 'assigned_to': self.other.pk})
        self.login(self.other)
        self.assertEqual(self.client.get(reverse('ticket_detail', args=[ticket.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('ticket_list'), {'scope': 'assigned'}).context['tickets'].paginator.count, 1)

    def test_manage_is_hr_only_and_notifies(self):
        self.raise_ticket(self.owner)
        ticket = Ticket.objects.get()
        self.login(self.owner)
        self.assertEqual(self.client.post(reverse('ticket_manage', args=[ticket.pk]), {'status': 'closed', 'priority': 'low'}).status_code, 403)
        self.login(self.hr)
        self.client.post(reverse('ticket_manage', args=[ticket.pk]), {'status': 'resolved', 'priority': 'low', 'assigned_to': self.hr.pk})
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.priority, ticket.assigned_to), ('resolved', 'low', self.hr))
        self.assertIsNotNone(ticket.resolved_at)
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='resolved').exists())
        self.client.post(reverse('ticket_manage', args=[ticket.pk]), {'status': 'in_progress', 'priority': 'low'})
        ticket.refresh_from_db()
        self.assertIsNone(ticket.resolved_at)
        self.client.post(reverse('ticket_manage', args=[ticket.pk]), {'status': 'bogus', 'priority': 'low'})
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'in_progress')

    def test_comments_and_reopen_on_reply(self):
        self.raise_ticket(self.owner)
        ticket = Ticket.objects.get()
        self.login(self.hr)
        self.client.post(reverse('ticket_comment', args=[ticket.pk]), {'body': 'Try rebooting'})
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='replied').exists())
        self.client.post(reverse('ticket_manage', args=[ticket.pk]), {'status': 'resolved', 'priority': 'high'})
        self.login(self.owner)
        self.client.post(reverse('ticket_comment', args=[ticket.pk]), {'body': 'Still broken'})
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'open')
        self.assertEqual(ticket.comments.count(), 2)
        self.client.post(reverse('ticket_comment', args=[ticket.pk]), {'body': '   '})
        self.assertEqual(ticket.comments.count(), 2)
        self.assertContains(self.client.get(reverse('ticket_detail', args=[ticket.pk])), 'Try rebooting')

    def test_close_only_by_raiser(self):
        self.raise_ticket(self.owner)
        ticket = Ticket.objects.get()
        self.login(self.hr)
        self.assertEqual(self.client.post(reverse('ticket_close', args=[ticket.pk])).status_code, 403)
        self.login(self.owner)
        self.client.post(reverse('ticket_close', args=[ticket.pk]))
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'closed')
        self.client.post(reverse('ticket_comment', args=[ticket.pk]), {'body': 'late'})
        self.assertEqual(ticket.comments.count(), 0)

    def test_list_filters(self):
        self.raise_ticket(self.owner)
        self.raise_ticket(self.owner, subject='Need payslip copy', category='finance')
        self.login(self.hr)
        for q, expected in (({'category': 'finance'}, 1), ({'status': 'active'}, 2), ({'q': 'payslip'}, 1),
                            ({'scope': 'mine'}, 0), ({'status': 'zzz', 'category': 'zzz', 'scope': 'zzz'}, 2),
                            ({'page': 'abc'}, 2)):
            r = self.client.get(reverse('ticket_list'), q)
            self.assertEqual(r.status_code, 200, q)
            self.assertEqual(r.context['tickets'].paginator.count, expected, q)
        self.assertEqual(self.client.get(reverse('ticket_list')).context['unassigned'], 2)


@TMP_MEDIA
class RecruitmentTests(HRBase):
    def setUp(self):
        super().setUp()
        self.login(self.hr)
        self.client.post(reverse('job_add'), {'title': 'Accountant', 'positions': 2, 'status': 'open'})
        self.job = JobOpening.objects.get()

    def test_job_pages(self):
        self.assertEqual(self.client.get(reverse('job_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('job_detail', args=[self.job.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('job_list'), {'status': 'zzz'}).status_code, 200)
        self.assertEqual(self.client.post(reverse('job_add'), {'title': '', 'positions': 0}).status_code, 200)
        self.client.post(reverse('job_edit', args=[self.job.pk]), {'title': 'Senior Accountant', 'positions': 1, 'status': 'on_hold'})
        self.job.refresh_from_db()
        self.assertEqual((self.job.title, self.job.status), ('Senior Accountant', 'on_hold'))

    def test_candidate_flow(self):
        r = self.client.post(reverse('candidate_add', args=[self.job.pk]), {
            'name': 'Ravi Kumar', 'email': 'ravi@example.com', 'expected_salary': '40000',
            'resume': SimpleUploadedFile('cv.pdf', b'%PDF-1.4 cv'),
        })
        self.assertEqual(r.status_code, 302)
        c = Candidate.objects.get()
        self.assertEqual((c.stage, c.job), ('applied', self.job))
        self.client.post(reverse('candidate_stage', args=[c.pk]), {'stage': 'interview'})
        c.refresh_from_db()
        self.assertEqual(c.stage, 'interview')
        self.client.post(reverse('candidate_stage', args=[c.pk]), {'stage': 'bogus'})
        c.refresh_from_db()
        self.assertEqual(c.stage, 'interview')
        page = self.client.get(reverse('job_detail', args=[self.job.pk]))
        self.assertContains(page, 'Ravi Kumar')
        self.client.post(reverse('candidate_stage', args=[c.pk]), {'stage': 'hired'})
        self.assertEqual(self.client.get(reverse('job_list')).context['jobs'][0].hired, 1)
        self.client.post(reverse('candidate_delete', args=[c.pk]))
        self.assertEqual(Candidate.objects.count(), 0)

    def test_candidate_validation(self):
        for data in ({'name': ' '}, {'name': 'A', 'email': 'nope'}, {'name': 'A', 'expected_salary': '-5'},
                     {'name': 'A', 'resume': SimpleUploadedFile('cv.exe', b'x')}):
            self.assertEqual(self.client.post(reverse('candidate_add', args=[self.job.pk]), data).status_code, 200, data)
        self.assertEqual(Candidate.objects.count(), 0)

    def test_delete_job_with_candidates(self):
        Candidate.objects.create(job=self.job, name='X')
        r = self.client.get(reverse('job_delete', args=[self.job.pk]))
        self.assertContains(r, '1 candidate')
        self.client.post(reverse('job_delete', args=[self.job.pk]))
        self.assertEqual((JobOpening.objects.count(), Candidate.objects.count()), (0, 0))

    def test_employees_blocked(self):
        c = Candidate.objects.create(job=self.job, name='X')
        for user in (self.owner, self.manager):
            self.login(user)
            for name, args in (('job_detail', [self.job.pk]), ('candidate_edit', [c.pk]), ('job_edit', [self.job.pk]),
                               ('candidate_add', [self.job.pk])):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)
            for name in ('candidate_stage', 'candidate_delete'):
                self.assertEqual(self.client.post(reverse(name, args=[c.pk]), {'stage': 'hired'}).status_code, 403)
            self.assertEqual(self.client.post(reverse('job_delete', args=[self.job.pk])).status_code, 403)
        c.refresh_from_db()
        self.assertEqual(c.stage, 'applied')


class ReviewTests(HRBase):
    def setUp(self):
        super().setUp()
        profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        profile.reporting_manager = self.manager
        profile.save()
        self.login(self.hr)
        today = timezone.localdate()
        self.client.post(reverse('cycle_add'), {'name': 'H1', 'start_date': today.isoformat(), 'end_date': (today + datetime.timedelta(days=30)).isoformat()})
        self.cycle = ReviewCycle.objects.get()
        self.client.post(reverse('cycle_generate', args=[self.cycle.pk]))

    def review(self, user):
        return Review.objects.get(cycle=self.cycle, employee=user)

    def test_generate_everyone_once(self):
        total = User.objects.filter(is_active=True).count()
        self.assertEqual(self.cycle.reviews.count(), total)
        self.assertEqual(self.review(self.owner).reviewer, self.manager)
        self.assertIsNone(self.review(self.other).reviewer)
        self.client.post(reverse('cycle_generate', args=[self.cycle.pk]))
        self.assertEqual(self.cycle.reviews.count(), total)
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='H1').exists())

    def test_cycle_dates_validated(self):
        today = timezone.localdate()
        r = self.client.post(reverse('cycle_add'), {'name': 'Bad', 'start_date': today.isoformat(), 'end_date': (today - datetime.timedelta(days=1)).isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ReviewCycle.objects.count(), 1)

    def test_self_review_once(self):
        review = self.review(self.owner)
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse('review_detail', args=[review.pk])).status_code, 200)
        bad = self.client.post(reverse('review_self', args=[review.pk]), {'self_rating': '9', 'self_comment': 'x'})
        self.assertEqual(bad.status_code, 302)
        review.refresh_from_db()
        self.assertIsNone(review.self_submitted_at)
        self.client.post(reverse('review_self', args=[review.pk]), {'self_rating': '4', 'self_comment': 'Shipped things'})
        review.refresh_from_db()
        self.assertEqual(review.self_rating, 4)
        self.assertTrue(Notification.objects.filter(user=self.manager, message__contains='self review').exists())
        self.client.post(reverse('review_self', args=[review.pk]), {'self_rating': '1', 'self_comment': 'changed'})
        review.refresh_from_db()
        self.assertEqual((review.self_rating, review.self_comment), (4, 'Shipped things'))

    def test_manager_review_and_visibility(self):
        review = self.review(self.owner)
        self.login(self.owner)
        self.client.post(reverse('review_manager', args=[review.pk]), {'manager_rating': '5', 'manager_comment': 'x'})
        review.refresh_from_db()
        self.assertIsNone(review.manager_submitted_at)  # employee khud ko review nahi de sakta
        self.login(self.manager)
        self.client.post(reverse('review_manager', args=[review.pk]), {'manager_rating': '3', 'manager_comment': 'Solid year', 'goals': 'Lead a project'})
        review.refresh_from_db()
        self.assertEqual(review.manager_rating, 3)
        self.assertTrue(Notification.objects.filter(user=self.owner, message__contains='ready').exists())
        self.client.post(reverse('review_manager', args=[review.pk]), {'manager_rating': '1', 'manager_comment': 'again'})
        review.refresh_from_db()
        self.assertEqual(review.manager_rating, 3)
        self.login(self.owner)
        self.assertContains(self.client.get(reverse('review_detail', args=[review.pk])), 'Solid year')

    def test_employee_does_not_see_manager_feedback_before_submit(self):
        review = self.review(self.owner)
        review.manager_comment = 'Secret draft'
        review.manager_rating = 2
        review.save()
        self.login(self.owner)
        self.assertNotContains(self.client.get(reverse('review_detail', args=[review.pk])), 'Secret draft')

    def test_privacy(self):
        review = self.review(self.owner)
        self.login(self.other)
        self.assertEqual(self.client.get(reverse('review_detail', args=[review.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('review_self', args=[review.pk]), {'self_rating': '3', 'self_comment': 'x'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('review_manager', args=[review.pk]), {}).status_code, 404)
        self.assertEqual(self.client.get(reverse('cycle_detail', args=[self.cycle.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('review_reviewer', args=[review.pk]), {'reviewer': self.other.pk}).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse('review_detail', args=[review.pk])).status_code, 200)

    def test_hr_reviews_when_no_reviewer_and_reassign(self):
        review = self.review(self.other)
        self.login(self.hr)
        self.client.post(reverse('review_reviewer', args=[review.pk]), {'reviewer': self.other.pk})  # khud ko nahi
        review.refresh_from_db()
        self.assertIsNone(review.reviewer)
        self.client.post(reverse('review_reviewer', args=[review.pk]), {'reviewer': 'zz'})
        self.client.post(reverse('review_reviewer', args=[review.pk]), {'reviewer': self.manager.pk})
        review.refresh_from_db()
        self.assertEqual(review.reviewer, self.manager)
        self.client.post(reverse('review_reviewer', args=[review.pk]), {'reviewer': ''})
        self.client.post(reverse('review_manager', args=[review.pk]), {'manager_rating': '4', 'manager_comment': 'HR view'})
        review.refresh_from_db()
        self.assertEqual((review.manager_rating, review.reviewer), (4, self.hr))

    def test_closed_cycle_blocks_submissions(self):
        review = self.review(self.owner)
        self.client.post(reverse('cycle_toggle', args=[self.cycle.pk]))
        self.cycle.refresh_from_db()
        self.assertFalse(self.cycle.is_open)
        self.login(self.owner)
        self.client.post(reverse('review_self', args=[review.pk]), {'self_rating': '4', 'self_comment': 'late'})
        review.refresh_from_db()
        self.assertIsNone(review.self_submitted_at)
        self.login(self.hr)
        self.client.post(reverse('cycle_generate', args=[self.cycle.pk]))
        self.assertEqual(self.client.get(reverse('cycle_detail', args=[self.cycle.pk])).status_code, 200)

    def test_pages_and_summary(self):
        review = self.review(self.owner)
        review.manager_rating = 4
        review.manager_submitted_at = timezone.now()
        review.save()
        self.login(self.hr)
        r = self.client.get(reverse('cycle_detail', args=[self.cycle.pk]))
        self.assertEqual(r.context['average'], 4)
        self.assertContains(self.client.get(reverse('review_home')), 'H1')
        self.login(self.manager)
        self.assertEqual(len(self.client.get(reverse('review_home')).context['to_give']), 1)

    def test_delete_cycle(self):
        self.client.post(reverse('cycle_delete', args=[self.cycle.pk]))
        self.assertEqual((ReviewCycle.objects.count(), Review.objects.count()), (0, 0))

    def test_inactive_manager_not_used(self):
        self.manager.is_active = False
        self.manager.save()
        extra = User.objects.create_user('newbie', 'n@example.com', 'pass12345')
        self.client.post(reverse('cycle_generate', args=[self.cycle.pk]))
        self.assertTrue(Review.objects.filter(cycle=self.cycle, employee=extra).exists())


class DashboardTodoTests(HRBase):
    def test_todos_show_and_clear(self):
        self.login(self.owner)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'Waiting for you')
        doc = CompanyDocument.objects.create(title='Code of conduct', file='documents/x.pdf', requires_ack=True)
        Ticket.objects.create(subject='S', description='D', raised_by=self.other, assigned_to=self.owner)
        Visitor.objects.create(name='V', host=self.owner, expected_on=timezone.localdate())
        r = self.client.get(reverse('dashboard'))
        self.assertContains(r, 'Waiting for you')
        self.assertEqual(len(r.context['todo_items']), 3)
        self.client.post(reverse('document_ack', args=[doc.pk]))
        self.assertEqual(len(self.client.get(reverse('dashboard')).context['todo_items']), 2)

    def test_review_todos(self):
        cycle = ReviewCycle.objects.create(name='C', start_date=timezone.localdate(), end_date=timezone.localdate())
        Review.objects.create(cycle=cycle, employee=self.owner, reviewer=self.manager)
        self.login(self.owner)
        self.assertIn('Submit your self review', [t for t, _ in self.client.get(reverse('dashboard')).context['todo_items']])
        self.login(self.manager)
        self.assertEqual(len(self.client.get(reverse('dashboard')).context['todo_items']), 1)
