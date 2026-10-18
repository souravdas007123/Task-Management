import calendar
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.validators import (
    FileExtensionValidator,
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import models
from django.urls import reverse
from django.utils import timezone

from .validators import ALLOWED_ATTACHMENT_EXTENSIONS, ALLOWED_RECEIPT_EXTENSIONS, validate_file_size


def add_months(day, months=1):
    """Date mein months jodta hai; mahine ke aakhri din ko clamp karta hai (31 Jan + 1 = 28/29 Feb)."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(day.day, last_day))


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('member', 'Member'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='member')
    # Labour cost ke liye: kaam ke ek ghante ki cost. Sirf admin/manager dekhte hain.
    hourly_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='Cost of one hour of this person\'s work. Used to calculate labour cost from logged time.',
    )
    # Workload page par utilization = is hafte ka kaam / capacity
    weekly_capacity_hours = models.PositiveSmallIntegerField(
        default=40, validators=[MinValueValidator(1), MaxValueValidator(168)],
        help_text='Hours of work this person can take on per week.',
    )

    def __str__(self):
        return f'{self.user.username} ({self.get_role_display()})'


class Project(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    budget = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='Total money available for this project. Leave empty if there is no budget.',
    )
    # 0, 80 ya 100: budget alert kis level tak bhej chuke hain (baar-baar notification na jaye)
    budget_alerted_level = models.PositiveSmallIntegerField(default=0, editable=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_projects')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Label(models.Model):
    name = models.CharField(max_length=40, unique=True)
    color = models.CharField(
        max_length=7,
        default='#7c8cff',
        validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Enter a color like #7c8cff.')],
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Task(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('In Progress', 'In Progress'),
        ('Completed', 'Completed'),
        ('On Hold', 'On Hold')
    ]

    PRIORITY_CHOICES = [
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High'),
    ]

    RECURRENCE_CHOICES = [
        ('none', 'Does not repeat'),
        ('daily', 'Every day'),
        ('weekly', 'Every week'),
        ('monthly', 'Every month'),
    ]

    # CSS ke liye short names (dashboard, board, report mein same colors)
    STATUS_CSS = {
        'Completed': 'completed',
        'In Progress': 'progress',
        'Pending': 'pending',
        'On Hold': 'hold',
    }

    title = models.CharField(max_length=200)
    description = models.TextField()
    assigned_to = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tasks')
    # SET_NULL: creator user delete ho to doosron ko assigned tasks delete na hon
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_tasks', null=True, blank=True)
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    labels = models.ManyToManyField(Label, blank=True, related_name='tasks')

    # Issue related image upload
    issue_image = models.ImageField(upload_to='issue_images/', blank=True, null=True, validators=[validate_file_size])

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='Medium')

    # Progress chart ke liye percentage field (0 to 100)
    progress = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    # Kitne ghante lagne ka andaza (workload planning aur estimate vs actual ke liye)
    estimated_hours = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)

    recurrence = models.CharField(max_length=10, choices=RECURRENCE_CHOICES, default='none')
    # Ek task ke complete hone par agla task sirf ek baar banta hai
    recurrence_spawned = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('task_detail', args=[self.pk])

    @property
    def status_css(self):
        return self.STATUS_CSS.get(self.status, 'hold')

    @property
    def priority_css(self):
        return self.priority.lower()

    @property
    def is_overdue(self):
        return bool(self.due_date and self.status != 'Completed' and self.due_date < timezone.localdate())

    def next_due_date(self):
        base = self.due_date or timezone.localdate()
        if self.recurrence == 'daily':
            return base + datetime.timedelta(days=1)
        if self.recurrence == 'weekly':
            return base + datetime.timedelta(days=7)
        if self.recurrence == 'monthly':
            return add_months(base, 1)
        return None

    def save(self, *args, **kwargs):
        previous_status = None
        if self.pk:
            previous_status = Task.objects.filter(pk=self.pk).values_list('status', flat=True).first()

        # completed_at hamesha status ke saath sync rehta hai (admin/API/board sab jagah)
        if self.status == 'Completed':
            if self.completed_at is None:
                self.completed_at = timezone.now()
        else:
            self.completed_at = None

        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {'completed_at'}

        super().save(*args, **kwargs)

        if (
            self.status == 'Completed'
            and previous_status not in (None, 'Completed')
            and self.recurrence != 'none'
            and not self.recurrence_spawned
        ):
            self.spawn_next_occurrence()

    def spawn_next_occurrence(self):
        next_due = self.next_due_date()
        # Start date bhi utna hi aage khiskta hai jitna due date
        next_start = None
        if self.start_date and self.due_date and next_due:
            next_start = self.start_date + (next_due - self.due_date)

        new_task = Task.objects.create(
            title=self.title,
            description=self.description,
            assigned_to=self.assigned_to,
            created_by=self.created_by,
            project=self.project,
            priority=self.priority,
            recurrence=self.recurrence,
            start_date=next_start,
            due_date=next_due,
            estimated_hours=self.estimated_hours,
            status='Pending',
            progress=0,
        )
        new_task.labels.set(self.labels.all())
        ChecklistItem.objects.bulk_create([
            ChecklistItem(task=new_task, text=item.text, position=item.position)
            for item in self.checklist.all()
        ])
        ActivityLog.objects.create(
            task=new_task,
            actor=None,
            message=f'Created automatically because the repeating task "{self.title}" was completed',
        )
        Task.objects.filter(pk=self.pk).update(recurrence_spawned=True)
        self.recurrence_spawned = True
        return new_task

    def sync_progress_from_checklist(self):
        """Checklist ho to progress = done items ka percentage."""
        total = self.checklist.count()
        if total:
            done = self.checklist.filter(is_done=True).count()
            self.progress = round(done * 100 / total)
            self.save(update_fields=['progress'])


class ChecklistItem(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='checklist')
    text = models.CharField(max_length=200)
    is_done = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return self.text


class Comment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='task_comments')
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'Comment on {self.task_id}'


class Attachment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(
        upload_to='attachments/',
        validators=[FileExtensionValidator(ALLOWED_ATTACHMENT_EXTENSIONS), validate_file_size],
    )
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='attachments')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at', '-id']

    def __str__(self):
        return self.original_name or self.file.name


class TimeEntry(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='time_entries')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='time_entries')
    minutes = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(24 * 60)])
    note = models.CharField(max_length=200, blank=True)
    worked_on = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-worked_on', '-id']

    def __str__(self):
        return f'{self.minutes} min on task {self.task_id}'

    @property
    def duration_display(self):
        hours, minutes = divmod(self.minutes, 60)
        if hours and minutes:
            return f'{hours}h {minutes}m'
        return f'{hours}h' if hours else f'{minutes}m'


class ActivityLog(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='activity')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='activity')
    message = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.message


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    message = models.CharField(max_length=300)
    link = models.CharField(max_length=200, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.message


class Expense(models.Model):
    """Project ka kharcha. Budget mein sirf 'approved' expenses ginte hain."""
    CATEGORY_CHOICES = [
        ('software', 'Software and tools'),
        ('hardware', 'Hardware and equipment'),
        ('services', 'Vendors and services'),
        ('travel', 'Travel'),
        ('marketing', 'Marketing'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('submitted', 'Waiting for review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    # PROTECT: kharche ka record project delete hone par chupchap ud na jaye
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name='expenses')
    task = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    title = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    incurred_on = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=300, blank=True)
    receipt = models.FileField(
        upload_to='receipts/', blank=True,
        validators=[FileExtensionValidator(ALLOWED_RECEIPT_EXTENSIONS), validate_file_size],
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='submitted')
    submitted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses_submitted')
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses_reviewed')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-incurred_on', '-id']

    def __str__(self):
        return f'{self.title} ({self.amount})'
