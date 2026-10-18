from django import forms
from django.contrib.auth.models import User
from django.core.validators import FileExtensionValidator
from django.utils import timezone

from .models import Attachment, ChecklistItem, Comment, Expense, Label, Project, Task, TimeEntry
from .validators import ALLOWED_ATTACHMENT_EXTENSIONS, validate_file_size


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            'title', 'description', 'assigned_to', 'project', 'labels', 'issue_image',
            'status', 'priority', 'progress', 'start_date', 'due_date', 'estimated_hours', 'recurrence',
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
        }
        help_texts = {
            'recurrence': 'When a repeating task is completed, the next one is created automatically.',
            'start_date': 'Optional. Used for the timeline.',
            'estimated_hours': 'How many hours you expect this to take. Used for workload planning.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Sirf active users ko assign kar sakte hain (par edit ke time purana assignee bana rahe)
        assignees = User.objects.filter(is_active=True)
        if self.instance.pk:
            assignees = User.objects.filter(is_active=True) | User.objects.filter(pk=self.instance.assigned_to_id)
        self.fields['assigned_to'].queryset = assignees.distinct().order_by('username')
        self.fields['project'].empty_label = 'No project'
        self.fields['labels'].required = False
        self.fields['recurrence'].required = False

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
        return cleaned


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'description', 'budget']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'budget': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
        }


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
