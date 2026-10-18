"""Aane wale birthdays aur work anniversaries."""
import datetime

from django.contrib.auth.models import User

WINDOW_DAYS = 14


def _on_year(day, year):
    """`day` ko `year` mein laata hai; 29 Feb non-leap year mein 28 Feb ban jata hai."""
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def _next_occurrence(day, today):
    this_year = _on_year(day, today.year)
    return this_year if this_year >= today else _on_year(day, today.year + 1)


def upcoming(today, days=WINDOW_DAYS):
    """Agle `days` din ke events (aaj samet), date ke hisaab se. Har item ek dict."""
    events = []
    users = User.objects.filter(is_active=True, profile__isnull=False).select_related('profile')
    for user in users:
        profile = user.profile
        if profile.date_of_birth:
            when = _next_occurrence(profile.date_of_birth, today)
            if (when - today).days <= days:
                events.append({'user': user, 'kind': 'birthday', 'date': when, 'years': None})
        if profile.joining_date and profile.joining_date < today:
            when = _next_occurrence(profile.joining_date, today)
            years = when.year - profile.joining_date.year
            if years >= 1 and (when - today).days <= days:
                events.append({'user': user, 'kind': 'anniversary', 'date': when, 'years': years})
    events.sort(key=lambda e: (e['date'], e['kind'], e['user'].username))
    for event in events:
        event['days_away'] = (event['date'] - today).days
    return events
