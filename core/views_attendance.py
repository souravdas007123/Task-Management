"""Attendance: apna check-in/out, team report (HR/admin sab, manager apni team), HR correction."""
import csv
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .attendance import DAY_LABELS, late_cutoff, month_data, work_start
from .forms import AttendanceEditForm
from .models import Attendance
from .permissions import hr_required, is_hr, is_manager
from .views_hr import _month_key, _parse_month, _shift_month, month_label
from .views_reports import _csv_safe


def _name(user):
    return user.get_full_name() or user.username


def visible_people(viewer):
    """Jinki attendance viewer dekh sakta hai: HR/admin sab, manager sirf apni team, baaki koi nahi."""
    people = User.objects.filter(is_active=True).select_related('profile').order_by('username')
    if is_hr(viewer):
        return people
    if is_manager(viewer):
        return people.filter(profile__reporting_manager=viewer)
    return people.none()


@login_required
def attendance_me(request):
    today = timezone.localdate()
    year, month = _parse_month(request.GET.get('month'))
    if (year, month) > (today.year, today.month):
        year, month = today.year, today.month
    rows, summary = month_data([request.user], year, month, today)[request.user.pk]
    record_today = Attendance.objects.filter(user=request.user, date=today).first()
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)
    return render(request, 'core/attendance.html', {
        'record': record_today,
        'can_check_in': record_today is None,
        'can_check_out': record_today is not None and record_today.check_out is None,
        'rows': list(reversed(rows)),
        'summary': summary,
        'label': month_label(year, month),
        'prev_key': _month_key(prev_year, prev_month),
        'next_key': _month_key(next_year, next_month) if (next_year, next_month) <= (today.year, today.month) else None,
        'start': work_start(),
        'cutoff': late_cutoff(),
        'now': timezone.localtime(),
        'can_view_team': bool(visible_people(request.user).exists()) or is_hr(request.user),
    })


@login_required
@require_POST
def check_in(request):
    now = timezone.now()
    _record, created = Attendance.objects.get_or_create(
        user=request.user, date=timezone.localdate(now), defaults={'check_in': now},
    )
    if created:
        messages.success(request, f'Checked in at {timezone.localtime(now):%H:%M}.')
    else:
        messages.error(request, 'You have already checked in today.')
    return redirect('attendance_me')


@login_required
@require_POST
def check_out(request):
    now = timezone.now()
    record = Attendance.objects.filter(user=request.user, date=timezone.localdate(now)).first()
    if record is None:
        messages.error(request, 'Check in first.')
    elif record.check_out is not None:
        messages.error(request, 'You have already checked out today.')
    else:
        record.check_out = max(now, record.check_in)
        record.save(update_fields=['check_out'])
        messages.success(request, f'Checked out at {timezone.localtime(now):%H:%M}. Worked {record.hours:.1f} hours.')
    return redirect('attendance_me')


def _team_context(request):
    today = timezone.localdate()
    year, month = _parse_month(request.GET.get('month'))
    people = list(visible_people(request.user))
    data = month_data(people, year, month, today)
    rows = [{'user': u, 'summary': data[u.pk][1]} for u in people]
    return year, month, rows


@login_required
def attendance_team(request):
    if not (is_hr(request.user) or is_manager(request.user)):
        raise Http404
    year, month, rows = _team_context(request)
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)
    return render(request, 'core/attendance_team.html', {
        'rows': rows,
        'label': month_label(year, month),
        'month_key': _month_key(year, month),
        'prev_key': _month_key(prev_year, prev_month),
        'next_key': _month_key(next_year, next_month),
        'whole_company': is_hr(request.user),
    })


@login_required
def attendance_team_export(request):
    if not (is_hr(request.user) or is_manager(request.user)):
        raise Http404
    year, month, rows = _team_context(request)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="attendance-{_month_key(year, month)}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['Employee', 'Month', 'Present days', 'Half days', 'Late', 'Absent', 'On leave', 'No check-out', 'Hours'])
    for row in rows:
        s = row['summary']
        writer.writerow([_csv_safe(_name(row['user'])), _month_key(year, month), s['present'], s['half'],
                         s['late'], s['absent'], s['leave'], s['no_checkout'], s['hours']])
    return response


@login_required
def attendance_person(request, user_id):
    # Dikhne wali list mein na ho to 404: pata hi na chale ki wo exist karta hai
    person = get_object_or_404(visible_people(request.user), pk=user_id)
    today = timezone.localdate()
    year, month = _parse_month(request.GET.get('month'))
    rows, summary = month_data([person], year, month, today)[person.pk]
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)
    return render(request, 'core/attendance_person.html', {
        'person': person, 'rows': list(reversed(rows)), 'summary': summary,
        'label': month_label(year, month), 'month_key': _month_key(year, month),
        'prev_key': _month_key(prev_year, prev_month), 'next_key': _month_key(next_year, next_month),
        'hr': is_hr(request.user),
    })


def _parse_day(value):
    try:
        day = datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise Http404
    if not (2000 <= day.year <= 2100) or day > timezone.localdate():
        raise Http404
    return day


@login_required
@hr_required
def attendance_edit(request, user_id, day):
    person = get_object_or_404(User, pk=user_id)
    date = _parse_day(day)
    record = Attendance.objects.filter(user=person, date=date).first()
    if request.method == 'POST':
        form = AttendanceEditForm(request.POST, instance=record, day=date)
        if form.is_valid():
            saved = form.save(commit=False)
            saved.user, saved.date, saved.edited_by = person, date, request.user
            saved.save()
            messages.success(request, f'Attendance of {_name(person)} for {date:%d %b %Y} saved.')
            return redirect(reverse('attendance_person', args=[person.pk]) + f'?month={_month_key(date.year, date.month)}')
    else:
        initial = {}
        if record is None:
            initial['check_in'] = timezone.make_aware(datetime.datetime.combine(date, work_start()))
        form = AttendanceEditForm(instance=record, day=date, initial=initial)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': f'Attendance: {_name(person)}, {date:%d %b %Y}',
        'back_url': 'attendance_team',
        'help': 'Fix a missing or wrong check-in or check-out. Times are in the office time zone.',
    })


@login_required
@hr_required
@require_POST
def attendance_delete(request, pk):
    record = get_object_or_404(Attendance, pk=pk)
    person_id, month_key = record.user_id, _month_key(record.date.year, record.date.month)
    record.delete()
    messages.success(request, 'Attendance entry deleted.')
    return redirect(reverse('attendance_person', args=[person_id]) + f'?month={month_key}')
