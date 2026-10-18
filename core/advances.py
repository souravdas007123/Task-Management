"""Salary advance: EMI ka hisab aur payslip mein katauti. Paisa Decimal mein."""
import datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone

from .budget import money
from .models import Advance, AdvanceInstallment, SalaryStructure

ZERO = Decimal('0.00')


def emi(advance):
    """Har kisht (aakhri ko chhodkar; aakhri mein jo bacha wo jata hai taaki paisa bilkul barabar rahe)."""
    return money(advance.amount / advance.installments)


def allocated(advance):
    return advance.deductions.aggregate(t=Sum('amount'))['t'] or ZERO


def repaid(advance):
    """Sirf published payslips ki katauti jo employee ko dikh chuki hai."""
    return advance.deductions.filter(payslip__status='published').aggregate(t=Sum('amount'))['t'] or ZERO


def balance(advance):
    return money(advance.amount - repaid(advance))


def is_open(advance):
    """Pending, ya approved par abhi poori kati nahi."""
    if advance.status == 'pending':
        return True
    return advance.status == 'approved' and allocated(advance) < advance.amount


def open_advance(user):
    for advance in Advance.objects.filter(user=user, status__in=('pending', 'approved')):
        if is_open(advance):
            return advance
    return None


def max_amount(user):
    """Zyada se zyada kitna advance mil sakta hai: ADVANCE_MAX_MONTHS ki gross salary. Structure na ho to None."""
    structure = SalaryStructure.objects.filter(
        user=user, effective_from__lte=timezone.localdate() + datetime.timedelta(days=31),
    ).order_by('-effective_from', '-id').first()
    if structure is None:
        return None
    return money((structure.basic + structure.hra + structure.allowances) * settings.ADVANCE_MAX_MONTHS)


def attach_advances(payslip):
    """Draft payslip ke liye is mahine ki EMI nikalta hai (pehle is slip ki purani EMI hata deta hai)."""
    AdvanceInstallment.objects.filter(payslip=payslip).delete()
    first_of_month = datetime.date(payslip.year, payslip.month, 1)
    total = ZERO
    for advance in Advance.objects.filter(user=payslip.user, status='approved', first_month__lte=first_of_month):
        earlier = advance.deductions.filter(
            Q(payslip__year__lt=payslip.year) | Q(payslip__year=payslip.year, payslip__month__lt=payslip.month)
        )
        paid = earlier.aggregate(t=Sum('amount'))['t'] or ZERO
        remaining = money(advance.amount - paid)
        if remaining <= 0:
            continue
        if earlier.count() + 1 >= advance.installments:
            amount = remaining
        else:
            amount = min(emi(advance), remaining)
        AdvanceInstallment.objects.create(advance=advance, payslip=payslip, amount=amount)
        total += amount
    payslip.advance_deduction = money(total)
    payslip.save(update_fields=['advance_deduction'])
    return payslip.advance_deduction
