"""Due/overdue tasks ke liye notification aur email bhejta hai.

Roz ek baar chalao (cron ya Task Scheduler se):
    python manage.py send_due_reminders            # kal tak due aur overdue
    python manage.py send_due_reminders --days 3   # agle 3 din mein due aur overdue

Ek hi din mein ek task ka ek hi reminder jata hai, to command dobara chalane par duplicate nahi banta.
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Notification, Task
from core.services import notify


class Command(BaseCommand):
    help = 'Send in-app and email reminders for tasks that are due soon or overdue.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=1, help='Remind for tasks due within this many days (default 1).')

    def handle(self, *args, **options):
        days = max(options['days'], 0)
        today = timezone.localdate()
        horizon = today + datetime.timedelta(days=days)

        tasks = (
            Task.objects.exclude(status='Completed')
            .filter(due_date__isnull=False, due_date__lte=horizon)
            .select_related('assigned_to')
        )

        sent = 0
        for task in tasks:
            if task.due_date < today:
                message = f'Overdue: "{task.title}" was due on {task.due_date:%d %b %Y}'
            elif task.due_date == today:
                message = f'Due today: "{task.title}"'
            else:
                message = f'Due {task.due_date:%d %b %Y}: "{task.title}"'

            already_sent = Notification.objects.filter(
                user=task.assigned_to, message=message, created_at__date=today,
            ).exists()
            if already_sent:
                continue

            if notify(task.assigned_to, message, link=task.get_absolute_url()):
                sent += 1

        self.stdout.write(self.style.SUCCESS(f'Sent {sent} reminder(s).'))
