"""Payslip ka hisab. Paisa Decimal mein, 2 decimal tak ROUND_HALF_UP."""
from decimal import Decimal

from django.utils import timezone

from .budget import money
from .hr import month_bounds, policies, unpaid_days_in_month, working_days
from .models import Payslip, SalaryStructure

ZERO = Decimal('0.00')


def structure_for(user, year, month):
    """Us mahine ke liye lagu salary structure (mahine ke aakhri din tak jo effective ho)."""
    _first, last = month_bounds(year, month)
    return SalaryStructure.objects.filter(user=user, effective_from__lte=last).order_by('-effective_from', '-id').first()


def current_structure(user):
    return structure_for(user, *_today_ym())


def _today_ym():
    today = timezone.localdate()
    return today.year, today.month


def compute(user, year, month, structure=None, policy=None):
    """Payslip ke structure-based fields. Structure na ho to None."""
    structure = structure or structure_for(user, year, month)
    if structure is None:
        return None
    first, last = month_bounds(year, month)
    month_working = working_days(first, last)
    unpaid = min(unpaid_days_in_month(user, year, month, policy), month_working)
    gross = money(structure.basic + structure.hra + structure.allowances)
    per_day = (gross / month_working) if month_working else ZERO
    return {
        'basic': money(structure.basic),
        'hra': money(structure.hra),
        'allowances': money(structure.allowances),
        'pf': money(structure.basic * structure.pf_percent / Decimal(100)),
        'tax': money(structure.tax),
        'other_deduction': money(structure.other_deduction),
        'working_days': month_working,
        'unpaid_days': unpaid,
        'leave_deduction': money(per_day * unpaid),
    }


def eligible(user, year, month):
    """Payslip ban sakti hai: active, joining date mahine ke baad ki na ho, aur structure ho."""
    profile = getattr(user, 'profile', None)
    _first, last = month_bounds(year, month)
    if profile and profile.joining_date and profile.joining_date > last:
        return False
    return structure_for(user, year, month) is not None


def generate_month(users, year, month, generated_by):
    """Draft payslips banata hai. Jinki slip pehle se hai unhe chhoot deta hai. (created, skipped) return."""
    policy = policies()
    existing = set(Payslip.objects.filter(year=year, month=month).values_list('user_id', flat=True))
    created = skipped = 0
    for user in users:
        if user.pk in existing or not eligible(user, year, month):
            skipped += 1
            continue
        values = compute(user, year, month, policy=policy)
        Payslip.objects.create(user=user, year=year, month=month, generated_by=generated_by, **values)
        created += 1
    return created, skipped


def recalculate(payslip):
    """Draft slip ko dobara hisab (leave/salary badli ho to). Bonus aur HR ki katauti bani rehti hai."""
    values = compute(payslip.user, payslip.year, payslip.month)
    if values is None:
        return False
    for key, value in values.items():
        setattr(payslip, key, value)
    payslip.save(update_fields=list(values))
    return True
