from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

User = get_user_model()


class UsernameForm(forms.ModelForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )

    class Meta:
        model = User
        fields = ('username',)

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('current_user')
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data['username']
        qs = User.objects.filter(username__iexact=username).exclude(pk=self.current_user.pk)
        if qs.exists():
            raise forms.ValidationError('That username is already taken.')
        return username


class EmailChangeForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'form-input'})
    )

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('current_user')
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data['email']
        if email.lower() == self.current_user.email.lower():
            raise forms.ValidationError('That is already your current email address.')
        qs = User.objects.filter(email__iexact=email).exclude(pk=self.current_user.pk)
        if qs.exists():
            raise forms.ValidationError('An account with that email already exists.')
        return email


class LoginEmailForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'placeholder': 'you@example.com',
            'class': 'form-input',
            'autofocus': True,
        })
    )


class LoginCodeForm(forms.Form):
    code = forms.CharField(
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={
            'placeholder': '6-digit code',
            'class': 'form-input form-input-code',
            'inputmode': 'numeric',
            'autocomplete': 'one-time-code',
            'autofocus': True,
        })
    )


class SignUpForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'placeholder': 'you@example.com',
            'class': 'form-input',
        })
    )
    username = forms.CharField(
        widget=forms.TextInput(attrs={
            'placeholder': 'Choose a username',
            'class': 'form-input',
        })
    )
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Create a password',
            'class': 'form-input',
        })
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm your password',
            'class': 'form-input',
        })
    )
    agree_terms = forms.BooleanField(
        required=True,
        label='I agree to the Terms of Service and Privacy Policy',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'})
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2', 'agree_terms')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user
