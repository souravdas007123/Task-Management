"""Forms for rooms, visitors, documents, helpdesk, recruitment and reviews."""
import datetime

from django import forms
from django.contrib.auth.models import User
from django.utils import timezone

from .models import (
    Candidate, CompanyDocument, JobOpening, Review, ReviewCycle, Room, Ticket, TicketComment, Visitor,
)

TEXT = {'class': 'form-control'}


def person_label(user):
    return user.get_full_name() or user.username


def active_users():
    return User.objects.filter(is_active=True).order_by('username')


def date_widget():
    return forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})


# ---------------------------------------------------------------- rooms

class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ['name', 'location', 'capacity', 'amenities', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs=TEXT),
            'location': forms.TextInput(attrs=TEXT),
            'capacity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'amenities': forms.TextInput(attrs=TEXT),
        }
        error_messages = {'name': {'unique': 'A room with this name already exists.'}}


class RoomBookingForm(forms.Form):
    MAX_HOURS = 12

    room = forms.ModelChoiceField(queryset=Room.objects.none(), widget=forms.Select(attrs=TEXT))
    title = forms.CharField(max_length=150, widget=forms.TextInput(attrs={**TEXT, 'placeholder': 'e.g. Sprint planning'}))
    day = forms.DateField(label='Date', widget=date_widget())
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}))
    end_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room'].queryset = Room.objects.filter(is_active=True)
        self.start = self.end = None

    def clean_title(self):
        title = self.cleaned_data['title'].strip()
        if not title:
            raise forms.ValidationError('Enter a title.')
        return title

    def clean(self):
        data = super().clean()
        day, start, end = data.get('day'), data.get('start_time'), data.get('end_time')
        if not (day and start and end):
            return data
        if end <= start:
            self.add_error('end_time', 'The end time must be after the start time.')
            return data
        self.start = timezone.make_aware(datetime.datetime.combine(day, start))
        self.end = timezone.make_aware(datetime.datetime.combine(day, end))
        if self.end < timezone.now():
            self.add_error('day', 'This time has already passed.')
        elif (self.end - self.start) > datetime.timedelta(hours=self.MAX_HOURS):
            self.add_error('end_time', f'A booking cannot be longer than {self.MAX_HOURS} hours.')
        return data


# ---------------------------------------------------------------- visitors

class VisitorForm(forms.ModelForm):
    class Meta:
        model = Visitor
        fields = ['name', 'company', 'phone', 'host', 'purpose', 'expected_on']
        widgets = {
            'name': forms.TextInput(attrs=TEXT),
            'company': forms.TextInput(attrs=TEXT),
            'phone': forms.TextInput(attrs=TEXT),
            'host': forms.Select(attrs=TEXT),
            'purpose': forms.TextInput(attrs={**TEXT, 'placeholder': 'Meeting, interview, delivery...'}),
            'expected_on': date_widget(),
        }

    def __init__(self, *args, user=None, can_choose_host=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['host'].queryset = active_users()
        self.fields['host'].label_from_instance = person_label
        if not can_choose_host:
            del self.fields['host']

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if not name:
            raise forms.ValidationError('Enter the visitor name.')
        return name

    def clean_expected_on(self):
        value = self.cleaned_data['expected_on']
        if self.instance.pk is None and value < timezone.localdate():
            raise forms.ValidationError('The date cannot be in the past.')
        return value


# ---------------------------------------------------------------- documents

class DocumentForm(forms.ModelForm):
    class Meta:
        model = CompanyDocument
        fields = ['title', 'category', 'description', 'file', 'requires_ack']
        widgets = {
            'title': forms.TextInput(attrs=TEXT),
            'category': forms.Select(attrs=TEXT),
            'description': forms.TextInput(attrs=TEXT),
        }

    def clean_title(self):
        title = self.cleaned_data['title'].strip()
        if not title:
            raise forms.ValidationError('Enter a title.')
        return title


# ---------------------------------------------------------------- helpdesk

class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['category', 'priority', 'subject', 'description']
        widgets = {
            'category': forms.Select(attrs=TEXT),
            'priority': forms.Select(attrs=TEXT),
            'subject': forms.TextInput(attrs={**TEXT, 'placeholder': 'Short summary of the problem'}),
            'description': forms.Textarea(attrs={**TEXT, 'rows': 5}),
        }

    def clean_subject(self):
        value = self.cleaned_data['subject'].strip()
        if not value:
            raise forms.ValidationError('Enter a subject.')
        return value

    def clean_description(self):
        value = self.cleaned_data['description'].strip()
        if not value:
            raise forms.ValidationError('Describe the problem.')
        return value


class TicketCommentForm(forms.ModelForm):
    class Meta:
        model = TicketComment
        fields = ['body']
        widgets = {'body': forms.Textarea(attrs={**TEXT, 'rows': 3, 'placeholder': 'Write a reply'})}

    def clean_body(self):
        value = self.cleaned_data['body'].strip()
        if not value:
            raise forms.ValidationError('Write something first.')
        return value


class TicketManageForm(forms.Form):
    status = forms.ChoiceField(choices=Ticket.STATUS_CHOICES, widget=forms.Select(attrs=TEXT))
    priority = forms.ChoiceField(choices=Ticket.PRIORITY_CHOICES, widget=forms.Select(attrs=TEXT))
    assigned_to = forms.ModelChoiceField(queryset=User.objects.none(), required=False, empty_label='Unassigned', widget=forms.Select(attrs=TEXT))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].queryset = active_users()
        self.fields['assigned_to'].label_from_instance = person_label


# ---------------------------------------------------------------- recruitment

class JobForm(forms.ModelForm):
    class Meta:
        model = JobOpening
        fields = ['title', 'department', 'positions', 'location', 'status', 'description']
        widgets = {
            'title': forms.TextInput(attrs=TEXT),
            'department': forms.Select(attrs=TEXT),
            'positions': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'location': forms.TextInput(attrs=TEXT),
            'status': forms.Select(attrs=TEXT),
            'description': forms.Textarea(attrs={**TEXT, 'rows': 5}),
        }


class CandidateForm(forms.ModelForm):
    class Meta:
        model = Candidate
        fields = ['name', 'email', 'phone', 'source', 'expected_salary', 'resume', 'interview_on', 'notes']
        widgets = {
            'name': forms.TextInput(attrs=TEXT),
            'email': forms.EmailInput(attrs=TEXT),
            'phone': forms.TextInput(attrs=TEXT),
            'source': forms.TextInput(attrs=TEXT),
            'expected_salary': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
            'interview_on': date_widget(),
            'notes': forms.Textarea(attrs={**TEXT, 'rows': 4}),
        }

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if not name:
            raise forms.ValidationError('Enter the candidate name.')
        return name


# ---------------------------------------------------------------- performance reviews

class ReviewCycleForm(forms.ModelForm):
    class Meta:
        model = ReviewCycle
        fields = ['name', 'start_date', 'end_date']
        widgets = {'name': forms.TextInput(attrs={**TEXT, 'placeholder': 'e.g. Annual review 2026'}),
                   'start_date': date_widget(), 'end_date': date_widget()}

    def clean(self):
        data = super().clean()
        if data.get('start_date') and data.get('end_date') and data['end_date'] < data['start_date']:
            self.add_error('end_date', 'The end date cannot be before the start date.')
        return data


RATING_CHOICES = [('', 'Select a rating')] + [
    (1, '1 - Needs improvement'), (2, '2 - Below expectations'), (3, '3 - Meets expectations'),
    (4, '4 - Exceeds expectations'), (5, '5 - Outstanding'),
]


class SelfReviewForm(forms.ModelForm):
    self_rating = forms.TypedChoiceField(choices=RATING_CHOICES, coerce=int, label='Your rating', widget=forms.Select(attrs=TEXT))

    class Meta:
        model = Review
        fields = ['self_rating', 'self_comment']
        labels = {'self_comment': 'What did you achieve this period?'}
        widgets = {'self_comment': forms.Textarea(attrs={**TEXT, 'rows': 5})}

    def clean_self_comment(self):
        value = self.cleaned_data['self_comment'].strip()
        if not value:
            raise forms.ValidationError('Write a few lines about your work.')
        return value


class ManagerReviewForm(forms.ModelForm):
    manager_rating = forms.TypedChoiceField(choices=RATING_CHOICES, coerce=int, label='Rating', widget=forms.Select(attrs=TEXT))

    class Meta:
        model = Review
        fields = ['manager_rating', 'manager_comment', 'goals']
        labels = {'manager_comment': 'Feedback'}
        widgets = {
            'manager_comment': forms.Textarea(attrs={**TEXT, 'rows': 5}),
            'goals': forms.Textarea(attrs={**TEXT, 'rows': 3}),
        }

    def clean_manager_comment(self):
        value = self.cleaned_data['manager_comment'].strip()
        if not value:
            raise forms.ValidationError('Write some feedback.')
        return value
