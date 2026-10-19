"""Sign up, HR ke account banane ke forms."""
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.validators import validate_email

from . import signup
from .models import Department, UserProfile

TEXT = {'class': 'form-control'}


def email_taken(email, exclude_pk=None):
    qs = User.objects.filter(email__iexact=email)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


class SignupForm(UserCreationForm):
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs=TEXT))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=TEXT))
    email = forms.EmailField(max_length=254, widget=forms.EmailInput(attrs=TEXT))
    # Bots ke liye jaal: insaan ko ye dikhta nahi
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={'tabindex': '-1', 'autocomplete': 'off'}))

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'first_name', 'last_name', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('username', 'password1', 'password2'):
            self.fields[name].widget.attrs.setdefault('class', 'form-control')
        self.fields['username'].help_text = 'Letters, digits and @ . + - _ only.'
        self.fields['password1'].help_text = ''
        self.fields['password2'].help_text = ''

    @property
    def is_bot(self):
        return bool(self.cleaned_data.get('website'))

    def clean_first_name(self):
        value = self.cleaned_data['first_name'].strip()
        if not value:
            raise forms.ValidationError('Enter your name.')
        return value

    def clean_email(self):
        value = self.cleaned_data['email'].strip()
        if not signup.domain_allowed(value):
            allowed = ', '.join('@' + d for d in signup.settings.SIGNUP_ALLOWED_DOMAINS)
            raise forms.ValidationError(f'Use your work email ({allowed}).')
        if email_taken(value):
            raise forms.ValidationError('An account with this email already exists.')
        return value

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data.get('last_name', '')
        user.email = self.cleaned_data['email']
        user.is_active = signup.mode() == 'open'
        if commit:
            user.save()
        return user


class AddEmployeeForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs=TEXT))
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs=TEXT))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs=TEXT))
    email = forms.EmailField(widget=forms.EmailInput(attrs=TEXT))
    role = forms.ChoiceField(choices=[('member', 'Employee'), ('manager', 'Manager')], widget=forms.Select(attrs=TEXT))
    department = forms.ModelChoiceField(queryset=Department.objects.all(), required=False, empty_label='No department', widget=forms.Select(attrs=TEXT))
    designation = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs=TEXT))
    joining_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    password = forms.CharField(
        label='Temporary password', strip=False, widget=forms.PasswordInput(attrs={**TEXT, 'autocomplete': 'new-password'}),
        help_text='Share it with them privately. They must choose a new one when they first log in.',
    )

    def __init__(self, *args, can_make_hr=False, **kwargs):
        super().__init__(*args, **kwargs)
        if can_make_hr:
            self.fields['role'].choices = [('member', 'Employee'), ('manager', 'Manager'), ('hr', 'HR')]

    def clean_username(self):
        value = self.cleaned_data['username'].strip()
        validator = User._meta.get_field('username').validators
        for check in validator:
            check(value)
        if User.objects.filter(username__iexact=value).exists():
            raise forms.ValidationError('This username is already taken.')
        return value

    def clean_first_name(self):
        value = self.cleaned_data['first_name'].strip()
        if not value:
            raise forms.ValidationError('Enter the name.')
        return value

    def clean_email(self):
        value = self.cleaned_data['email'].strip()
        if email_taken(value):
            raise forms.ValidationError('An account with this email already exists.')
        return value

    def clean(self):
        data = super().clean()
        password = data.get('password')
        if password:
            candidate = User(username=data.get('username') or '', first_name=data.get('first_name') or '',
                             last_name=data.get('last_name') or '', email=data.get('email') or '')
            try:
                password_validation.validate_password(password, candidate)
            except forms.ValidationError as exc:
                self.add_error('password', exc)
        return data


def create_employee(data, created_by=None, active=True, must_change=True):
    """User + profile banata hai. `data` AddEmployeeForm.cleaned_data jaisa dict."""
    user = User.objects.create_user(
        username=data['username'], email=data['email'], password=data['password'],
        first_name=data['first_name'], last_name=data.get('last_name', ''),
    )
    user.is_active = active
    user.save(update_fields=['is_active'])
    UserProfile.objects.update_or_create(user=user, defaults={
        'role': data.get('role', 'member'), 'department': data.get('department'),
        'designation': data.get('designation', ''), 'joining_date': data.get('joining_date'),
        'must_change_password': must_change,
    })
    return user
