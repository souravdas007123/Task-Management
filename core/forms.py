from django import forms
from django.contrib.auth.models import User
from django.core.validators import FileExtensionValidator
from django.utils import timezone

from .models import (
    Attachment, ChecklistItem, Comment, Department, Expense, Holiday, Label, LeaveRequest, Payslip,
    Project, SalaryStructure, Task, TimeEntry, UserProfile,
)
from .hr import check_leave_request
from .permissions import visible_projects, visible_tasks
from .validators import ALLOWED_ATTACHMENT_EXTENSIONS, validate_file_size


def _reaches(start, target_pk, limit=500):
    """True agar `start` se blocked_by chain chalte hue target_pk mil jaye (loop check)."""
    seen = set()
    stack = [start.pk]
    while stack and len(seen) < limit:
        pk = stack.pop()
        if pk == target_pk:
            return True
        if pk in seen:
            continue
        seen.add(pk)
        stack.extend(Task.blocked_by.through.objects.filter(from_task_id=pk).values_list('to_task_id', flat=True))
    return False


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            'title', 'description', 'assigned_to', 'project', 'labels', 'issue_image',
            'status', 'priority', 'progress', 'start_date', 'due_date', 'estimated_hours', 'recurrence',
            'blocked_by',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'assigned_to': forms.Select(attrs={'class': 'form-control'}),
            'project': forms.Select(attrs={'class': 'form-control'}),
            'labels': forms.CheckboxSelectMultiple(attrs={'class': 'checks'}),
            'issue_image': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'priority': forms.Select(attrs={'class': 'form-control'}),
            'progress': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'max': '100'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'estimated_hours': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.25'}),
            'recurrence': forms.Select(attrs={'class': 'form-control'}),
            'blocked_by': forms.SelectMultiple(attrs={'class': 'form-control', 'size': 5}),
        }
        help_texts = {
            'blocked_by': 'This task cannot be completed until these tasks are completed. Hold Ctrl/Cmd to pick several.',
            'recurrence': 'When a repeating task is completed, the next one is created automatically.',
            'start_date': 'Optional. Used for the timeline.',
            'estimated_hours': 'How many hours you expect this to take. Used for workload planning.',
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Sirf active users ko assign kar sakte hain (par edit ke time purana assignee bana rahe)
        assignees = User.objects.filter(is_active=True)
        if self.instance.pk:
            assignees = User.objects.filter(is_active=True) | User.objects.filter(pk=self.instance.assigned_to_id)
        self.fields['assigned_to'].queryset = assignees.distinct().order_by('username')
        self.fields['project'].empty_label = 'No project'
        self.fields['labels'].required = False
        self.fields['recurrence'].required = False

        # Private projects aur unke tasks sirf unhe dikhein jinhe access hai (current value hamesha rahe)
        projects = Project.objects.all()
        blockers = Task.objects.exclude(status='Completed')
        if user is not None:
            projects = visible_projects(projects, user)
            blockers = visible_tasks(blockers, user)
        if self.instance.pk:
            if self.instance.project_id:
                projects = projects | Project.objects.filter(pk=self.instance.project_id)
            blockers = blockers.exclude(pk=self.instance.pk) | self.instance.blocked_by.all()
        self.fields['project'].queryset = projects.distinct()
        self.fields['blocked_by'].queryset = blockers.distinct().order_by('-created_at')
        self.fields['blocked_by'].required = False
        self.fields['blocked_by'].label_from_instance = lambda task: f'#{task.pk} {task.title}'

    def clean_recurrence(self):
        # Field na aaye to purani value rakho (edit) ya 'none' (naya task)
        value = self.cleaned_data.get('recurrence')
        if value:
            return value
        return self.instance.recurrence if self.instance.pk else 'none'

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_date')
        due = cleaned.get('due_date')
        if start and due and start > due:
            self.add_error('due_date', 'The due date cannot be before the start date.')

        blockers = cleaned.get('blocked_by')
        if blockers is not None:
            if self.instance.pk and any(_reaches(blocker, self.instance.pk) for blocker in blockers):
                self.add_error('blocked_by', 'These tasks would block each other in a loop.')
            elif cleaned.get('status') == 'Completed' and any(b.status != 'Completed' for b in blockers):
                self.add_error('status', 'Finish the blocking tasks before completing this one.')
        return cleaned


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'description', 'budget', 'is_private', 'members']
        widgets = {
            'members': forms.CheckboxSelectMultiple(attrs={'class': 'checks'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'budget': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
        }


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['members'].queryset = User.objects.filter(is_active=True).order_by('username')
        self.fields['members'].required = False


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['project', 'task', 'title', 'amount', 'category', 'incurred_on', 'note', 'receipt']
        widgets = {
            'project': forms.Select(attrs={'class': 'form-control'}),
            'task': forms.Select(attrs={'class': 'form-control'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'What was it for?'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'min': '0.01', 'step': '0.01'}),
            'category': forms.Select(attrs={'class': 'form-control'}),
            'incurred_on': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'note': forms.TextInput(attrs={'class': 'form-control'}),
            'receipt': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }
        labels = {'incurred_on': 'Date of expense', 'task': 'Related task'}
        help_texts = {
            'task': 'Optional. Must belong to the selected project.',
            'receipt': 'PDF or image, up to 10 MB.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].empty_label = 'Choose a project'
        self.fields['task'].empty_label = 'No related task'
        self.fields['task'].queryset = Task.objects.order_by('-created_at')
        self.fields['task'].label_from_instance = lambda task: f'#{task.pk} {task.title}'

    def clean(self):
        cleaned = super().clean()
        project = cleaned.get('project')
        task = cleaned.get('task')
        if project and task and task.project_id != project.pk:
            self.add_error('task', 'This task does not belong to the selected project.')
        return cleaned


class LabelForm(forms.ModelForm):
    class Meta:
        model = Label
        fields = ['name', 'color']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control color-input'}),
        }


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ['body']
        widgets = {
            'body': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Write a comment'}),
        }
        labels = {'body': 'Comment'}
        error_messages = {'body': {'required': 'Write something before posting.'}}

    def clean_body(self):
        body = (self.cleaned_data.get('body') or '').strip()
        if not body:
            raise forms.ValidationError('Write something before posting.')
        return body


class ChecklistItemForm(forms.ModelForm):
    class Meta:
        model = ChecklistItem
        fields = ['text']
        widgets = {
            'text': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Add a checklist item'}),
        }
        error_messages = {'text': {'required': 'Write the item before adding it.'}}

    def clean_text(self):
        text = (self.cleaned_data.get('text') or '').strip()
        if not text:
            raise forms.ValidationError('Write the item before adding it.')
        return text


class AttachmentForm(forms.Form):
    file = forms.FileField(
        validators=[FileExtensionValidator(ALLOWED_ATTACHMENT_EXTENSIONS), validate_file_size],
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'}),
    )


class TimeEntryForm(forms.Form):
    hours = forms.IntegerField(min_value=0, max_value=24, initial=0, widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'max': 24}))
    minutes = forms.IntegerField(min_value=0, max_value=59, initial=0, widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'max': 59}))
    note = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'What did you work on? (optional)'}))
    worked_on = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))

    def clean(self):
        cleaned = super().clean()
        hours = cleaned.get('hours')
        minutes = cleaned.get('minutes')
        if hours is None or minutes is None:
            return cleaned
        total = hours * 60 + minutes
        if total < 1:
            raise forms.ValidationError('Enter at least 1 minute.')
        if total > 24 * 60:
            raise forms.ValidationError('One entry cannot be more than 24 hours.')
        cleaned['total_minutes'] = total
        return cleaned

    def save(self, task, user):
        return TimeEntry.objects.create(
            task=task,
            user=user,
            minutes=self.cleaned_data['total_minutes'],
            note=self.cleaned_data.get('note', ''),
            worked_on=self.cleaned_data['worked_on'],
        )


class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ['kind', 'start_date', 'end_date', 'reason']
        widgets = {
            'kind': forms.Select(attrs={'class': 'form-control'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'reason': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_date')
        end = cleaned.get('end_date')
        if start and end:
            if end < start:
                self.add_error('end_date', 'The last day cannot be before the first day.')
            elif (end - start).days > 365:
                self.add_error('end_date', 'A leave request cannot be longer than a year.')
            elif self.user is not None and cleaned.get('kind'):
                problem = check_leave_request(self.user, cleaned['kind'], start, end)
                if problem:
                    raise forms.ValidationError(problem)
        return cleaned


class EmployeeForm(forms.ModelForm):
    """HR employee ki details badalta hai (salary yahan nahi)."""
    class Meta:
        model = UserProfile
        fields = [
            'designation', 'department', 'employee_id', 'employment_type', 'joining_date', 'probation_end',
            'reporting_manager', 'phone', 'emergency_name', 'emergency_phone',
        ]
        widgets = {
            'designation': forms.TextInput(attrs={'class': 'form-control'}),
            'department': forms.Select(attrs={'class': 'form-control'}),
            'employee_id': forms.TextInput(attrs={'class': 'form-control'}),
            'employment_type': forms.Select(attrs={'class': 'form-control'}),
            'joining_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'probation_end': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'reporting_manager': forms.Select(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'emergency_name': forms.TextInput(attrs={'class': 'form-control'}),
            'emergency_phone': forms.TextInput(attrs={'class': 'form-control'}),
        }
        labels = {'designation': 'Post / designation', 'emergency_name': 'Emergency contact name',
                  'emergency_phone': 'Emergency contact phone'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        managers = User.objects.filter(is_active=True).exclude(pk=self.instance.user_id).order_by('username')
        self.fields['reporting_manager'].queryset = managers
        self.fields['reporting_manager'].empty_label = 'Nobody'
        self.fields['department'].empty_label = 'No department'

    def clean_employee_id(self):
        value = (self.cleaned_data.get('employee_id') or '').strip()
        return value or None

    def clean(self):
        cleaned = super().clean()
        joining, probation = cleaned.get('joining_date'), cleaned.get('probation_end')
        if joining and probation and probation < joining:
            self.add_error('probation_end', 'The probation cannot end before the joining date.')
        boss = cleaned.get('reporting_manager')
        if boss and self.instance.user_id:
            # Loop check: boss ke upar chalte hue ye employee mil jaye to loop banega
            seen, current = set(), boss
            while current is not None and current.pk not in seen:
                if current.pk == self.instance.user_id:
                    self.add_error('reporting_manager', 'That would make a reporting loop.')
                    break
                seen.add(current.pk)
                profile = UserProfile.objects.filter(user=current).select_related('reporting_manager').first()
                current = profile.reporting_manager if profile else None
        return cleaned


class MyContactForm(forms.ModelForm):
    """Employee khud sirf apna phone aur emergency contact badal sakta hai."""
    class Meta:
        model = UserProfile
        fields = ['phone', 'emergency_name', 'emergency_phone']
        widgets = {
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'emergency_name': forms.TextInput(attrs={'class': 'form-control'}),
            'emergency_phone': forms.TextInput(attrs={'class': 'form-control'}),
        }
        labels = {'emergency_name': 'Emergency contact name', 'emergency_phone': 'Emergency contact phone'}


class SalaryStructureForm(forms.ModelForm):
    class Meta:
        model = SalaryStructure
        fields = ['effective_from', 'basic', 'hra', 'allowances', 'pf_percent', 'tax', 'other_deduction', 'note']
        widgets = {
            'effective_from': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'basic': forms.NumberInput(attrs={'class': 'form-control', 'min': '0.01', 'step': '0.01'}),
            'hra': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
            'allowances': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
            'pf_percent': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'max': '100', 'step': '0.01'}),
            'tax': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
            'other_deduction': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
            'note': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Yearly increment'}),
        }
        labels = {'basic': 'Basic salary (per month)', 'hra': 'HRA (per month)', 'allowances': 'Other allowances (per month)'}
        help_texts = {'effective_from': 'Applies to the whole month in which this date falls.'}

    def clean_basic(self):
        basic = self.cleaned_data['basic']
        if basic <= 0:
            raise forms.ValidationError('Basic salary must be more than zero.')
        return basic

    def clean(self):
        cleaned = super().clean()
        try:
            gross = cleaned['basic'] + cleaned['hra'] + cleaned['allowances']
            deductions = cleaned['basic'] * cleaned['pf_percent'] / 100 + cleaned['tax'] + cleaned['other_deduction']
        except (KeyError, TypeError):
            return cleaned
        if deductions > gross:
            raise forms.ValidationError('The monthly deductions are more than the gross salary.')
        return cleaned


class PayslipAdjustForm(forms.ModelForm):
    class Meta:
        model = Payslip
        fields = ['bonus', 'extra_deduction', 'adjustment_note']
        widgets = {
            'bonus': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
            'extra_deduction': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
            'adjustment_note': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Shown on the payslip'}),
        }
        labels = {'extra_deduction': 'Extra deduction'}

    def clean(self):
        cleaned = super().clean()
        bonus, extra = cleaned.get('bonus'), cleaned.get('extra_deduction')
        if bonus is not None and extra is not None:
            net = self.instance.gross + bonus - (self.instance.total_deductions - self.instance.extra_deduction) - extra
            if net < 0:
                raise forms.ValidationError('With these amounts the net pay would be negative.')
        return cleaned


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ['date', 'name']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Diwali'}),
        }
        error_messages = {'date': {'unique': 'There is already a holiday on this date.'}}


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name']
        widgets = {'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Sales'})}
        error_messages = {'name': {'unique': 'That department already exists.'}}
