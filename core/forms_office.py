"""Forms for rooms, visitors, documents, helpdesk, recruitment and reviews."""
import datetime

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from .models import (
    Advance, AttendanceRequest, Candidate, Claim, CompanyDocument, EmployeeDocument, JobOpening, Project, PurchaseRequest,
    Resignation, Review, ReviewCycle, Room, Ticket, TicketComment, Vendor, Visitor,
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


# ---------------------------------------------------------------- claims, attendance requests, resignation

class ClaimForm(forms.ModelForm):
    MAX_AGE_DAYS = 365

    class Meta:
        model = Claim
        fields = ['title', 'category', 'amount', 'incurred_on', 'note', 'receipt']
        widgets = {
            'title': forms.TextInput(attrs={**TEXT, 'placeholder': 'e.g. Cab to client site'}),
            'category': forms.Select(attrs=TEXT),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'min': '0.01', 'step': '0.01'}),
            'incurred_on': date_widget(),
            'note': forms.TextInput(attrs=TEXT),
        }
        labels = {'incurred_on': 'Spent on'}
        help_texts = {'receipt': 'PDF or image up to 10 MB. Attach it if you have one.'}

    def clean_title(self):
        value = self.cleaned_data['title'].strip()
        if not value:
            raise forms.ValidationError('Enter what the money was spent on.')
        return value

    def clean_incurred_on(self):
        value = self.cleaned_data['incurred_on']
        today = timezone.localdate()
        if value > today:
            raise forms.ValidationError('The date cannot be in the future.')
        if (today - value).days > self.MAX_AGE_DAYS:
            raise forms.ValidationError('Claims older than a year cannot be filed.')
        return value


class AttendanceRequestForm(forms.ModelForm):
    WINDOW_DAYS = 30

    class Meta:
        model = AttendanceRequest
        fields = ['kind', 'date', 'check_in_time', 'check_out_time', 'reason']
        widgets = {
            'kind': forms.Select(attrs=TEXT),
            'date': date_widget(),
            'check_in_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'check_out_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'reason': forms.TextInput(attrs=TEXT),
        }
        labels = {'check_in_time': 'Check-in time', 'check_out_time': 'Check-out time'}
        help_texts = {'check_in_time': 'Only for fixing a missed check-in/out.'}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_reason(self):
        value = self.cleaned_data['reason'].strip()
        if not value:
            raise forms.ValidationError('Give a short reason.')
        return value

    def clean(self):
        data = super().clean()
        kind, day = data.get('kind'), data.get('date')
        if not kind or not day:
            return data
        today = timezone.localdate()
        window = datetime.timedelta(days=self.WINDOW_DAYS)
        if kind == 'regularize':
            if day > today:
                self.add_error('date', 'You can only fix a day that has already started.')
            elif today - day > window:
                self.add_error('date', f'Only the last {self.WINDOW_DAYS} days can be fixed.')
            start, end = data.get('check_in_time'), data.get('check_out_time')
            if start is None:
                self.add_error('check_in_time', 'Enter the time you arrived.')
            if end is None:
                self.add_error('check_out_time', 'Enter the time you left.')
            if start and end and end <= start:
                self.add_error('check_out_time', 'The check-out must be after the check-in.')
            elif end and day == today and end > timezone.localtime().time():
                self.add_error('check_out_time', 'That time has not come yet.')
        else:
            if day < today:
                self.add_error('date', 'Work from home must be for today or a later day.')
            elif day - today > window:
                self.add_error('date', f'Work from home can be requested up to {self.WINDOW_DAYS} days ahead.')
            data['check_in_time'] = data['check_out_time'] = None
        if self.user is not None and not self.errors:
            clash = AttendanceRequest.objects.filter(
                user=self.user, kind=kind, date=day, status__in=('pending', 'approved'),
            ).exists()
            if clash:
                self.add_error('date', 'You already have a request for this day.')
        return data

    def save(self, commit=True):
        item = super().save(commit=False)
        if item.kind == 'wfh':
            item.check_in_time = item.check_out_time = None
        if commit:
            item.save()
        return item


class ResignationForm(forms.ModelForm):
    class Meta:
        model = Resignation
        fields = ['proposed_last_day', 'reason']
        widgets = {'proposed_last_day': date_widget(), 'reason': forms.TextInput(attrs=TEXT)}
        labels = {'proposed_last_day': 'Proposed last working day'}
        help_texts = {'proposed_last_day': 'HR confirms the final date based on your notice period.'}

    def clean_proposed_last_day(self):
        value = self.cleaned_data['proposed_last_day']
        if value < timezone.localdate():
            raise forms.ValidationError('The last day cannot be in the past.')
        if value > timezone.localdate() + datetime.timedelta(days=366):
            raise forms.ValidationError('Choose a date within a year.')
        return value


class ResignationDecisionForm(forms.Form):
    last_working_day = forms.DateField(required=False, widget=date_widget())
    hr_note = forms.CharField(required=False, max_length=300, widget=forms.TextInput(attrs=TEXT))


# ---------------------------------------------------------------- advances, employee documents, purchases

class AdvanceForm(forms.ModelForm):
    class Meta:
        model = Advance
        fields = ['amount', 'installments', 'reason']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'step': '0.01'}),
            'installments': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '36'}),
            'reason': forms.TextInput(attrs=TEXT),
        }
        labels = {'installments': 'Repay over (months)'}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_amount(self):
        from .advances import max_amount
        value = self.cleaned_data['amount']
        limit = max_amount(self.user) if self.user is not None else None
        if self.user is not None and limit is None:
            raise forms.ValidationError('Your salary is not set up yet. Ask HR.')
        if limit is not None and value > limit:
            raise forms.ValidationError(
                f'You can take at most {limit} ({settings.ADVANCE_MAX_MONTHS} months of gross salary).')
        return value

    def clean(self):
        data = super().clean()
        if self.user is not None and not self.errors:
            from .advances import open_advance
            if open_advance(self.user) is not None:
                raise forms.ValidationError('You already have an advance that is pending or not fully repaid.')
        return data


class EmployeeDocumentForm(forms.ModelForm):
    class Meta:
        model = EmployeeDocument
        fields = ['kind', 'title', 'reference', 'file', 'expires_on']
        widgets = {
            'kind': forms.Select(attrs=TEXT),
            'title': forms.TextInput(attrs={**TEXT, 'placeholder': 'Optional, e.g. Passport (India)'}),
            'reference': forms.TextInput(attrs=TEXT),
            'expires_on': date_widget(),
        }
        labels = {'expires_on': 'Expires on'}
        help_texts = {'file': 'PDF, image or Word file up to 10 MB. Only you and HR can open it.'}

    def clean_expires_on(self):
        value = self.cleaned_data.get('expires_on')
        if value and value.year < 2000:
            raise forms.ValidationError('Enter a valid date.')
        return value


class VendorForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = ['name', 'contact_person', 'email', 'phone', 'notes', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs=TEXT), 'contact_person': forms.TextInput(attrs=TEXT),
            'email': forms.EmailInput(attrs=TEXT), 'phone': forms.TextInput(attrs=TEXT),
            'notes': forms.TextInput(attrs=TEXT),
        }
        error_messages = {'name': {'unique': 'A vendor with this name already exists.'}}

    def clean_name(self):
        value = self.cleaned_data['name'].strip()
        if not value:
            raise forms.ValidationError('Enter the vendor name.')
        return value


class PurchaseForm(forms.ModelForm):
    class Meta:
        model = PurchaseRequest
        fields = ['title', 'category', 'description', 'quantity', 'unit_cost', 'project', 'vendor', 'needed_by']
        widgets = {
            'title': forms.TextInput(attrs={**TEXT, 'placeholder': 'e.g. 5 office chairs'}),
            'category': forms.Select(attrs=TEXT),
            'description': forms.TextInput(attrs=TEXT),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'unit_cost': forms.NumberInput(attrs={'class': 'form-control', 'min': '0.01', 'step': '0.01'}),
            'project': forms.Select(attrs=TEXT),
            'vendor': forms.Select(attrs=TEXT),
            'needed_by': date_widget(),
        }
        labels = {'unit_cost': 'Cost per item', 'project': 'Charge to project (optional)', 'vendor': 'Preferred vendor (optional)'}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .permissions import selectable_projects
        projects = Project.objects.all()
        self.fields['project'].queryset = selectable_projects(projects, user) if user is not None else projects
        self.fields['project'].empty_label = 'No project'
        self.fields['vendor'].queryset = Vendor.objects.filter(is_active=True)
        self.fields['vendor'].empty_label = 'Any vendor'

    def clean_title(self):
        value = self.cleaned_data['title'].strip()
        if not value:
            raise forms.ValidationError('Enter what you need.')
        return value

    def clean_needed_by(self):
        value = self.cleaned_data.get('needed_by')
        if value and value < timezone.localdate():
            raise forms.ValidationError('The date cannot be in the past.')
        return value
