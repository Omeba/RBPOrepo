from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import password_validation
from .models import User, Tag


class RegistrationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('username',)


class LoginForm(forms.Form):
    username = forms.CharField(label='Логин', max_length=150)
    password = forms.CharField(label='Пароль', strip=False, widget=forms.PasswordInput)


class CredentialsForm(forms.Form):
    current_password = forms.CharField(label='Текущий пароль', strip=False, widget=forms.PasswordInput)
    username = forms.CharField(label='Новый логин', max_length=150, validators=User._meta.get_field('username').validators)
    password1 = forms.CharField(label='Новый пароль (можно оставить пустым)', required=False, strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label='Повтор нового пароля', required=False, strip=False, widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        if data.get('password1') != data.get('password2'):
            raise forms.ValidationError('Новые пароли не совпадают.')
        return data


class TopicForm(forms.Form):
    title = forms.CharField(label='Название', max_length=200)
    description = forms.CharField(label='Описание', max_length=10000, widget=forms.Textarea)
    tags = forms.ModelMultipleChoiceField(label='Теги (хотя бы один)', queryset=Tag.objects.all())


class MessageForm(forms.Form):
    text = forms.CharField(label='Сообщение', max_length=10000, widget=forms.Textarea)


class TagForm(forms.Form):
    name = forms.CharField(label='Название тега', max_length=50)


class SearchForm(forms.Form):
    tags = forms.ModelMultipleChoiceField(label='Теги', required=False, queryset=Tag.objects.all())
