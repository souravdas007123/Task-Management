"""Onboarding/offboarding checklist: template ki lines se employee ki checklist banana."""
from django.db import transaction

from .models import ChecklistTask, ChecklistTemplate, EmployeeChecklist

MAX_ITEMS = 40

DEFAULT_TEMPLATES = {
    'onboarding': '\n'.join([
        'HR | Collect ID proof, address proof and bank details',
        'HR | Share the offer letter and sign the employment agreement',
        'HR | Add the employee record and salary in this system',
        'IT | Create email and software accounts',
        'IT | Hand over the laptop and accessories',
        'Manager | Introduce the team and assign a buddy',
        'Manager | Set goals for the first week',
        'Employee | Read the company policies',
        'Employee | Fill in contact and emergency details on My profile',
    ]),
    'offboarding': '\n'.join([
        'Manager | Plan the handover of work and open tasks',
        'Employee | Return the laptop, ID card and other assets',
        'IT | Disable email and software accounts',
        'HR | Hold the exit interview',
        'HR | Settle the final salary and leave balance',
        'HR | Issue the relieving letter',
    ]),
}

OWNERS = {name.lower(): name for name, _label in ChecklistTask.OWNER_CHOICES}


def parse_items(text):
    """"Owner | Title" lines -> [(owner, title)]. Owner na ho ya galat ho to HR."""
    items = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        owner, title = 'HR', line
        if '|' in line:
            head, _sep, rest = line.partition('|')
            if head.strip().lower() in OWNERS and rest.strip():
                owner, title = OWNERS[head.strip().lower()], rest.strip()
        items.append((owner, title[:200]))
    return items


def template_text(kind):
    row = ChecklistTemplate.objects.filter(kind=kind).first()
    return row.items if row else DEFAULT_TEMPLATES[kind]


def open_checklist(user, kind):
    return EmployeeChecklist.objects.filter(user=user, kind=kind, completed_at__isnull=True).first()


@transaction.atomic
def start_checklist(user, kind, started_by):
    """Naya checklist. Same kind ka adhura checklist ho to None (dobara nahi banta)."""
    if open_checklist(user, kind):
        return None
    checklist = EmployeeChecklist.objects.create(user=user, kind=kind, started_by=started_by)
    ChecklistTask.objects.bulk_create([
        ChecklistTask(checklist=checklist, owner=owner, title=title, position=index)
        for index, (owner, title) in enumerate(parse_items(template_text(kind)))
    ])
    return checklist
