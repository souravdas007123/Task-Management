"""Sign up ke niyam: mode, email domain aur ek IP se bahut sign up rokna."""
from django.conf import settings
from django.core.cache import cache

THROTTLE_SECONDS = 3600


def mode():
    value = settings.SIGNUP_MODE
    return value if value in ('approval', 'open', 'off') else 'approval'


def enabled():
    return mode() != 'off'


def domain_allowed(email):
    domains = settings.SIGNUP_ALLOWED_DOMAINS
    if not domains:
        return True
    return email.rsplit('@', 1)[-1].lower() in domains


def _key(ip):
    return f'signup-throttle:{ip}'


def throttled(ip):
    return (cache.get(_key(ip)) or 0) >= settings.SIGNUP_MAX_PER_HOUR


def record(ip):
    key = _key(ip)
    cache.add(key, 0, THROTTLE_SECONDS)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, THROTTLE_SECONDS)
