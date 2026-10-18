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


class Department(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


PHONE_VALIDATOR = RegexValidator(r'^[0-9+()\- ]{5,30}$', 'Enter a phone number using digits, spaces, + ( ) or -.')


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('hr', 'HR'),
        ('manager', 'Manager'),
        ('member', 'Member'),
    ]
    EMPLOYMENT_CHOICES = [
        ('full_time', 'Full-time'),
        ('part_time', 'Part-time'),
        ('contract', 'Contract'),
        ('intern', 'Intern'),
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

    # Employee details (HR bharta hai; directory mein designation/department sabko dikhte hain)
    designation = models.CharField(max_length=100, blank=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='members')
    employee_id = models.CharField(max_length=30, unique=True, null=True, blank=True)
    employment_type = models.CharField(max_length=10, choices=EMPLOYMENT_CHOICES, default='full_time')
    joining_date = models.DateField(null=True, blank=True)
    probation_end = models.DateField(null=True, blank=True)
    reporting_manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='direct_reports',
    )
    phone = models.CharField(max_length=30, blank=True, validators=[PHONE_VALIDATOR])
    emergency_name = models.CharField(max_length=100, blank=True)
    emergency_phone = models.CharField(max_length=30, blank=True, validators=[PHONE_VALIDATOR])

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
    # Private project ke tasks sirf members, managers aur us task ke assignee/creator ko dikhte hain
    is_private = models.BooleanField(
        default=False,
        help_text='Only the members below (and managers) can see the tasks of a private project.',
    )
    members = models.ManyToManyField(User, blank=True, related_name='member_projects')
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
    # Jab tak ye tasks complete na hon, ye task complete nahi ho sakta
    blocked_by = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='blocking')

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

    def open_blockers(self):
        """Wo tasks jo is task ko rok rahe hain (abhi complete nahi hue)."""
        if not self.pk:
            return Task.objects.none()
        return self.blocked_by.exclude(status='Completed')

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


class LeaveRequest(models.Model):
    KIND_CHOICES = [
        ('annual', 'Annual leave'),
        ('sick', 'Sick leave'),
        ('casual', 'Casual leave'),
        ('unpaid', 'Unpaid leave'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Waiting for review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='leave_requests')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='annual')
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='leave_reviewed')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date', '-id']

    def __str__(self):
        return f'{self.user_id}: {self.start_date} to {self.end_date}'

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


class Holiday(models.Model):
    """Office holiday: leave ke din ginte waqt aur payroll ke working days mein nahi ginti."""
    date = models.DateField(unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f'{self.name} ({self.date})'


class LeavePolicy(models.Model):
    """Har leave type ka saalana quota. days_per_year = 0 ka matlab quota nahi (unlimited)."""
    kind = models.CharField(max_length=10, choices=LeaveRequest.KIND_CHOICES, unique=True)
    days_per_year = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(366)])
    is_paid = models.BooleanField(default=True)

    class Meta:
        ordering = ['kind']

    def __str__(self):
        return self.get_kind_display()


class SalaryStructure(models.Model):
    """Mahine ki salary ka structure. Naya revision = nayi row (purani history bani rehti hai).

    Jis mahine ki ye effective_from date aati hai, usi poore mahine se lagu hoti hai.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='salary_structures')
    effective_from = models.DateField()
    basic = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    hra = models.DecimalField('HRA', max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    pf_percent = models.DecimalField(
        'PF % of basic', max_digits=5, decimal_places=2, default=12,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    tax = models.DecimalField('Tax (TDS) per month', max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    other_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from', '-id']

    def __str__(self):
        return f'{self.user_id} from {self.effective_from}'

    @property
    def gross(self):
        return self.basic + self.hra + self.allowances


class Payslip(models.Model):
    """Ek mahine ki payslip. Values us waqt ki snapshot hain, baad mein salary badalne se purani slip nahi badalti."""
    STATUS_CHOICES = [('draft', 'Draft'), ('published', 'Published')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payslips')
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    basic = models.DecimalField(max_digits=12, decimal_places=2)
    hra = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pf = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    working_days = models.PositiveSmallIntegerField(default=0)
    unpaid_days = models.PositiveSmallIntegerField(default=0)
    leave_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # HR ke haath se: bonus aur extra katauti
    bonus = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    extra_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    adjustment_note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    generated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    generated_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-year', '-month', 'user__username']
        constraints = [models.UniqueConstraint(fields=['user', 'year', 'month'], name='one_payslip_per_month')]

    def __str__(self):
        return f'{self.user_id} {self.year}-{self.month:02d}'

    @property
    def gross(self):
        return self.basic + self.hra + self.allowances

    @property
    def total_deductions(self):
        return self.pf + self.tax + self.other_deduction + self.leave_deduction + self.extra_deduction

    @property
    def net(self):
        return self.gross + self.bonus - self.total_deductions

    @property
    def period(self):
        return datetime.date(self.year, self.month, 1)


class Attendance(models.Model):
    """Ek din ka ek check-in/check-out. date = check-in ki local date."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendance')
    date = models.DateField()
    check_in = models.DateTimeField()
    check_out = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    edited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-date']
        constraints = [models.UniqueConstraint(fields=['user', 'date'], name='one_attendance_per_day')]

    def __str__(self):
        return f'{self.user_id} {self.date}'

    @property
    def hours(self):
        if not self.check_out:
            return None
        return max((self.check_out - self.check_in).total_seconds(), 0) / 3600


class Announcement(models.Model):
    title = models.CharField(max_length=150)
    body = models.TextField(max_length=3000)
    is_pinned = models.BooleanField(default=False)
    expires_on = models.DateField(null=True, blank=True, help_text='Optional. Hidden from this date onwards.')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='announcements')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_pinned', '-created_at', '-id']

    def __str__(self):
        return self.title


class Asset(models.Model):
    CATEGORY_CHOICES = [
        ('laptop', 'Laptop'), ('phone', 'Phone'), ('monitor', 'Monitor'),
        ('accessory', 'Accessory'), ('furniture', 'Furniture'), ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('available', 'Available'), ('assigned', 'Assigned'), ('repair', 'In repair'), ('retired', 'Retired'),
    ]

    name = models.CharField(max_length=120)
    asset_tag = models.CharField(max_length=40, unique=True)
    category = models.CharField(max_length=12, choices=CATEGORY_CHOICES, default='laptop')
    serial_number = models.CharField(max_length=80, blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='available')
    notes = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['asset_tag']

    def __str__(self):
        return f'{self.asset_tag} {self.name}'

    @property
    def current_assignment(self):
        return self.assignments.filter(returned_on__isnull=True).select_related('user').first()


class AssetAssignment(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='assignments')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='asset_assignments')
    assigned_on = models.DateField(default=timezone.localdate)
    returned_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    return_note = models.CharField(max_length=200, blank=True)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-assigned_on', '-id']
        constraints = [
            # Ek asset ek waqt par sirf ek insaan ke paas
            models.UniqueConstraint(
                fields=['asset'], condition=models.Q(returned_on__isnull=True), name='one_active_assignment_per_asset',
            ),
        ]

    def __str__(self):
        return f'{self.asset_id} -> {self.user_id}'


class ChecklistTemplate(models.Model):
    """Onboarding/offboarding ki default list. items: har line "Owner | Title"."""
    KIND_CHOICES = [('onboarding', 'Onboarding'), ('offboarding', 'Offboarding')]
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, unique=True)
    items = models.TextField()

    def __str__(self):
        return self.get_kind_display()


class EmployeeChecklist(models.Model):
    KIND_CHOICES = ChecklistTemplate.KIND_CHOICES
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='checklists')
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    started_on = models.DateField(default=timezone.localdate)
    started_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_on', '-id']

    def __str__(self):
        return f'{self.kind} {self.user_id}'

    @property
    def is_complete(self):
        return self.completed_at is not None


class ChecklistTask(models.Model):
    OWNER_CHOICES = [('HR', 'HR'), ('IT', 'IT'), ('Manager', 'Manager'), ('Employee', 'Employee')]
    checklist = models.ForeignKey(EmployeeChecklist, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=200)
    owner = models.CharField(max_length=10, choices=OWNER_CHOICES, default='HR')
    position = models.PositiveSmallIntegerField(default=0)
    is_done = models.BooleanField(default=False)
    done_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return self.title


# ---------------------------------------------------------------- meeting rooms, visitors

class Room(models.Model):
    name = models.CharField(max_length=80, unique=True)
    location = models.CharField(max_length=120, blank=True, help_text='Floor or wing, e.g. 2nd floor east.')
    capacity = models.PositiveSmallIntegerField(default=6, validators=[MinValueValidator(1), MaxValueValidator(1000)])
    amenities = models.CharField(max_length=200, blank=True, help_text='e.g. Projector, whiteboard, video call')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class RoomBooking(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='bookings')
    booked_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='room_bookings')
    title = models.CharField(max_length=150)
    start = models.DateTimeField()
    end = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start', 'id']
        indexes = [models.Index(fields=['room', 'start'])]

    def __str__(self):
        return f'{self.room_id} {self.start:%Y-%m-%d %H:%M}'


class Visitor(models.Model):
    STATUS_CHOICES = [
        ('expected', 'Expected'), ('in', 'In the office'), ('out', 'Left'), ('cancelled', 'Cancelled'),
    ]
    name = models.CharField(max_length=120)
    company = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    host = models.ForeignKey(User, on_delete=models.CASCADE, related_name='visitors')
    purpose = models.CharField(max_length=200, blank=True)
    expected_on = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='expected')
    badge_no = models.CharField(max_length=20, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    registered_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-expected_on', '-id']

    def __str__(self):
        return self.name


# ---------------------------------------------------------------- company documents, helpdesk

DOCUMENT_EXTENSIONS = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'png', 'jpg', 'jpeg']


class CompanyDocument(models.Model):
    CATEGORY_CHOICES = [
        ('policy', 'Policy'), ('handbook', 'Handbook'), ('form', 'Form / template'),
        ('legal', 'Legal'), ('other', 'Other'),
    ]
    title = models.CharField(max_length=150)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES, default='policy')
    description = models.CharField(max_length=300, blank=True)
    file = models.FileField(
        upload_to='documents/',
        validators=[FileExtensionValidator(DOCUMENT_EXTENSIONS), validate_file_size],
    )
    version = models.PositiveSmallIntegerField(default=1)
    requires_ack = models.BooleanField(
        default=False, verbose_name='Everyone must acknowledge',
        help_text='Employees are asked to confirm they have read it. Uploading a new version asks again.',
    )
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'title']

    def __str__(self):
        return self.title


class DocumentAck(models.Model):
    document = models.ForeignKey(CompanyDocument, on_delete=models.CASCADE, related_name='acks')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='document_acks')
    version = models.PositiveSmallIntegerField()
    acknowledged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['document', 'user', 'version'], name='one_ack_per_version')]


class Ticket(models.Model):
    CATEGORY_CHOICES = [
        ('it', 'IT / equipment'), ('hr', 'HR'), ('admin', 'Admin / facilities'),
        ('finance', 'Finance / payroll'), ('other', 'Other'),
    ]
    PRIORITY_CHOICES = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High')]
    STATUS_CHOICES = [
        ('open', 'Open'), ('in_progress', 'In progress'), ('resolved', 'Resolved'), ('closed', 'Closed'),
    ]
    subject = models.CharField(max_length=150)
    description = models.TextField(max_length=3000)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES, default='it')
    priority = models.CharField(max_length=6, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='open')
    raised_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tickets')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tickets')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'#{self.pk} {self.subject}'

    @property
    def is_open(self):
        return self.status in ('open', 'in_progress')


class TicketComment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']


# ---------------------------------------------------------------- recruitment, performance

class JobOpening(models.Model):
    STATUS_CHOICES = [('open', 'Open'), ('on_hold', 'On hold'), ('closed', 'Closed')]
    title = models.CharField(max_length=120)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    positions = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(500)])
    location = models.CharField(max_length=120, blank=True)
    description = models.TextField(max_length=3000, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default='open')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.title


class Candidate(models.Model):
    STAGE_CHOICES = [
        ('applied', 'Applied'), ('screening', 'Screening'), ('interview', 'Interview'),
        ('offer', 'Offer'), ('hired', 'Hired'), ('rejected', 'Rejected'),
    ]
    job = models.ForeignKey(JobOpening, on_delete=models.CASCADE, related_name='candidates')
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    source = models.CharField(max_length=60, blank=True, help_text='Referral, LinkedIn, walk-in...')
    expected_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    resume = models.FileField(
        upload_to='resumes/', blank=True,
        validators=[FileExtensionValidator(['pdf', 'doc', 'docx']), validate_file_size],
    )
    stage = models.CharField(max_length=10, choices=STAGE_CHOICES, default='applied')
    notes = models.TextField(max_length=2000, blank=True)
    interview_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.name


class ReviewCycle(models.Model):
    name = models.CharField(max_length=80)
    start_date = models.DateField()
    end_date = models.DateField()
    is_open = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date', '-id']

    def __str__(self):
        return self.name


RATING_VALIDATORS = [MinValueValidator(1), MaxValueValidator(5)]


class Review(models.Model):
    cycle = models.ForeignKey(ReviewCycle, on_delete=models.CASCADE, related_name='reviews')
    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    reviewer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviews_to_give')
    self_rating = models.PositiveSmallIntegerField(null=True, blank=True, validators=RATING_VALIDATORS)
    self_comment = models.TextField(max_length=2000, blank=True)
    self_submitted_at = models.DateTimeField(null=True, blank=True)
    manager_rating = models.PositiveSmallIntegerField(null=True, blank=True, validators=RATING_VALIDATORS)
    manager_comment = models.TextField(max_length=2000, blank=True)
    goals = models.TextField(max_length=2000, blank=True, help_text='Goals for the next period.')
    manager_submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['employee__username']
        constraints = [models.UniqueConstraint(fields=['cycle', 'employee'], name='one_review_per_cycle')]

    def __str__(self):
        return f'{self.cycle_id} {self.employee_id}'

    @property
    def is_complete(self):
        return self.manager_submitted_at is not None
