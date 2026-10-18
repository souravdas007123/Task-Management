"""Meeting rooms aur visitor management."""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms_office import RoomBookingForm, RoomForm, VisitorForm
from .models import Room, RoomBooking, Visitor
from .permissions import hr_required, is_hr
from .services import notify

PAGE = 30


def parse_day(value, default=None):
    try:
        return datetime.date.fromisoformat((value or '').strip())
    except ValueError:
        return default


def day_bounds(day):
    start = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))
    return start, start + datetime.timedelta(days=1)


# ---------------------------------------------------------------- rooms

@login_required
def room_list(request):
    today = timezone.localdate()
    day = parse_day(request.GET.get('date'), today)
    start, end = day_bounds(day)
    hr = is_hr(request.user)
    rooms = list(Room.objects.all() if hr else Room.objects.filter(is_active=True))
    bookings = RoomBooking.objects.filter(start__lt=end, end__gt=start).select_related('booked_by')
    by_room = {room.pk: [] for room in rooms}
    for booking in bookings:
        if booking.room_id in by_room:
            booking.can_cancel = hr or booking.booked_by_id == request.user.id
            by_room[booking.room_id].append(booking)
    for room in rooms:
        room.day_bookings = by_room[room.pk]
    mine = RoomBooking.objects.filter(booked_by=request.user, end__gte=timezone.now()).select_related('room')[:10]
    return render(request, 'core/rooms.html', {
        'rooms': rooms, 'day': day, 'is_today': day == today, 'mine': mine, 'hr': hr,
        'prev_day': day - datetime.timedelta(days=1), 'next_day': day + datetime.timedelta(days=1),
    })


@login_required
def room_book(request):
    if request.method == 'POST':
        form = RoomBookingForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                room = Room.objects.select_for_update().get(pk=form.cleaned_data['room'].pk)
                clash = RoomBooking.objects.filter(room=room, start__lt=form.end, end__gt=form.start).first()
                if room.is_active and clash is None:
                    RoomBooking.objects.create(
                        room=room, booked_by=request.user, title=form.cleaned_data['title'],
                        start=form.start, end=form.end,
                    )
                    messages.success(request, f'Booked {room.name}.')
                    return redirect(f"{reverse('room_list')}?date={form.cleaned_data['day'].isoformat()}")
            if clash is not None:
                form.add_error(None, 'That room is already booked from {:%H:%M} to {:%H:%M}.'.format(
                    timezone.localtime(clash.start), timezone.localtime(clash.end)))
            else:
                form.add_error('room', 'This room is not available.')
    else:
        initial = {'day': parse_day(request.GET.get('date'), timezone.localdate())}
        room_id = request.GET.get('room')
        if room_id and room_id.isdigit():
            initial['room'] = int(room_id)
        form = RoomBookingForm(initial=initial)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Book a Room', 'back_url': 'room_list',
        'help': 'Pick a room and a time. A room cannot be booked twice for the same time.',
    })


@login_required
@require_POST
def booking_cancel(request, pk):
    booking = get_object_or_404(RoomBooking, pk=pk)
    if booking.booked_by_id != request.user.id and not is_hr(request.user):
        messages.error(request, 'Only the person who booked it can cancel.')
    else:
        booking.delete()
        messages.success(request, 'Booking cancelled.')
    return redirect('room_list')


@login_required
@hr_required
def room_add(request):
    form = RoomForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Room added.')
        return redirect('room_list')
    return render(request, 'core/simple_form.html', {'form': form, 'page_title': 'Add Room', 'back_url': 'room_list'})


@login_required
@hr_required
def room_edit(request, pk):
    room = get_object_or_404(Room, pk=pk)
    form = RoomForm(request.POST or None, instance=room)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Room updated.')
        return redirect('room_list')
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': f'Edit {room.name}', 'back_url': 'room_list',
        'help': 'Untick "Is active" to stop new bookings without losing history.',
    })


# ---------------------------------------------------------------- visitors

def _visible_visitors(user):
    qs = Visitor.objects.select_related('host')
    return qs if is_hr(user) else qs.filter(Q(host=user) | Q(registered_by=user))


@login_required
def visitor_list(request):
    hr = is_hr(request.user)
    today = timezone.localdate()
    visitors = _visible_visitors(request.user)
    status = request.GET.get('status')
    if status in dict(Visitor.STATUS_CHOICES):
        visitors = visitors.filter(status=status)
    else:
        status = ''
    query = (request.GET.get('q') or '').strip()
    if query:
        visitors = visitors.filter(Q(name__icontains=query) | Q(company__icontains=query) | Q(host__username__icontains=query))
    page_obj = Paginator(visitors, PAGE).get_page(request.GET.get('page'))
    inside = _visible_visitors(request.user).filter(status='in').count()
    expected_today = _visible_visitors(request.user).filter(status='expected', expected_on=today).count()
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'core/visitors.html', {
        'visitors': page_obj, 'page_obj': page_obj, 'querystring': params.urlencode(), 'hr': hr,
        'current': {'status': status, 'q': query}, 'status_choices': Visitor.STATUS_CHOICES,
        'inside': inside, 'expected_today': expected_today, 'today': today,
    })


@login_required
def visitor_add(request):
    hr = is_hr(request.user)
    if request.method == 'POST':
        form = VisitorForm(request.POST, user=request.user, can_choose_host=hr)
        if form.is_valid():
            visitor = form.save(commit=False)
            if not hr:
                visitor.host = request.user
            visitor.registered_by = request.user
            visitor.save()
            if visitor.host_id != request.user.id:
                notify(visitor.host, f'Visitor {visitor.name} is expected on {visitor.expected_on:%d %b}',
                       link=reverse('visitor_list'), actor=request.user, request=request)
            messages.success(request, 'Visitor registered.')
            return redirect('visitor_list')
    else:
        form = VisitorForm(user=request.user, can_choose_host=hr, initial={'host': request.user.pk})
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': 'Register a Visitor', 'back_url': 'visitor_list',
        'help': 'Reception checks the visitor in when they arrive and your host is notified.',
    })


def _visitor_for(user, pk):
    return get_object_or_404(_visible_visitors(user), pk=pk)


@login_required
@hr_required
@require_POST
def visitor_checkin(request, pk):
    visitor = get_object_or_404(Visitor, pk=pk)
    if visitor.status != 'expected':
        messages.error(request, 'Only an expected visitor can be checked in.')
        return redirect('visitor_list')
    visitor.status = 'in'
    visitor.checked_in_at = timezone.now()
    visitor.badge_no = (request.POST.get('badge_no') or '').strip()[:20]
    visitor.save(update_fields=['status', 'checked_in_at', 'badge_no'])
    notify(visitor.host, f'Your visitor {visitor.name} has arrived', link=reverse('visitor_list'),
           actor=request.user, request=request)
    messages.success(request, f'{visitor.name} checked in.')
    return redirect('visitor_list')


@login_required
@hr_required
@require_POST
def visitor_checkout(request, pk):
    visitor = get_object_or_404(Visitor, pk=pk)
    if visitor.status != 'in':
        messages.error(request, 'This visitor is not in the office.')
        return redirect('visitor_list')
    visitor.status = 'out'
    visitor.checked_out_at = timezone.now()
    visitor.save(update_fields=['status', 'checked_out_at'])
    messages.success(request, f'{visitor.name} checked out.')
    return redirect('visitor_list')


@login_required
@require_POST
def visitor_cancel(request, pk):
    visitor = _visitor_for(request.user, pk)
    if visitor.status != 'expected':
        messages.error(request, 'Only an expected visit can be cancelled.')
    else:
        visitor.status = 'cancelled'
        visitor.save(update_fields=['status'])
        messages.success(request, 'Visit cancelled.')
    return redirect('visitor_list')
