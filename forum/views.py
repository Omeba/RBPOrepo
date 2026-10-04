from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import forms, services
from .models import Topic, Message, Tag


def fields_only(*allowed):
    """Reject forged authors, target accounts, roles and unexpected parameters."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method == 'POST':
                if request.content_type not in ('application/x-www-form-urlencoded', 'multipart/form-data'):
                    return HttpResponseBadRequest('Поддерживаются только HTML-формы.')
                if request.FILES:
                    return HttpResponseBadRequest('Вложения не поддерживаются.')
                permitted = {'csrfmiddlewaretoken', *allowed}
                if set(request.POST) - permitted:
                    return HttpResponseBadRequest('Недопустимые поля запроса.')
                if any(len(request.POST.getlist(k)) > 1 for k in request.POST if k != 'tags'):
                    return HttpResponseBadRequest('Повторяющиеся поля запроса.')
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def form_page(request, form, title, status=200):
    return render(request, 'forum/form.html', {'form': form, 'title': title}, status=status)


def execute_form(request, form, action, success):
    if request.method == 'POST' and form.is_valid():
        try:
            result = action(form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        except IntegrityError:
            form.add_error(None, 'Не удалось сохранить данные. Возможно, такой логин или тег уже существует.')
        else:
            return success(result)
    return None


@require_GET
def health(request):
    Tag.objects.exists()  # Verify the database and migrations, not just HTTP routing.
    return JsonResponse({'status': 'ok', 'service': 'humAIn'})


@require_GET
def home(request):
    top = Topic.objects.annotate(message_count=Count('messages')).order_by('-message_count', '-created_at', '-pk')[:3]
    target = parse_datetime(settings.COUNTDOWN_TARGET) if settings.COUNTDOWN_TARGET else None
    if target and timezone.is_naive(target):
        target = None
    return render(request, 'forum/home.html', {'topics': top, 'countdown_target': target})


@require_GET
def search(request):
    form = forms.SearchForm(request.GET)
    topics = Topic.objects.none()
    if form.is_valid():
        ids = [tag.pk for tag in form.cleaned_data['tags']]
        topics = Topic.objects.select_related('author').prefetch_related('tags').annotate(
            message_count=Count('messages', distinct=True),
            matched_tags=Count('tags', filter=Q(tags__pk__in=ids), distinct=True))
        if ids:
            topics = topics.filter(matched_tags__gt=0)
        # Match count dominates. Within ties: activity decays with age, in hours.
        now = timezone.now()
        topics = list(topics)
        for topic in topics:
            age = max(0, (now - topic.created_at).total_seconds() / 3600)
            topic.rank = (topic.message_count + 1) / (age + 2)
        topics.sort(key=lambda t: (t.matched_tags, t.rank, t.created_at, t.pk), reverse=True)
    page = Paginator(topics, 20).get_page(request.GET.get('page'))
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'forum/search.html', {'form': form, 'page_obj': page, 'query': query.urlencode()},
                  status=200 if form.is_valid() else 400)


@require_GET
def topic_detail(request, pk):
    topic = get_object_or_404(Topic.objects.select_related('author').prefetch_related('tags'), pk=pk)
    page = Paginator(topic.messages.select_related('author'), 30).get_page(request.GET.get('page'))
    for item in page:
        item.deletable = services.can_delete(request.user, item)
    return render(request, 'forum/topic.html', {'topic': topic, 'page_obj': page,
        'deletable': services.can_delete(request.user, topic), 'form': forms.MessageForm()})


@require_http_methods(['GET', 'POST'])
@fields_only('username', 'password1', 'password2')
def register(request):
    form = forms.RegistrationForm(request.POST if request.method == 'POST' else None)
    response = execute_form(request, form, lambda data: form.save(), lambda _: redirect('login'))
    return response or form_page(request, form, 'Регистрация', 400 if request.method == 'POST' else 200)


@require_http_methods(['GET', 'POST'])
@fields_only('username', 'password')
def sign_in(request):
    form = forms.LoginForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        if services.sign_in(request, **form.cleaned_data):
            return redirect('home')
        form.add_error(None, 'Неверный логин или пароль.')
    return form_page(request, form, 'Вход', 400 if request.method == 'POST' else 200)


@require_POST
@fields_only()
def sign_out(request):
    logout(request)
    return redirect('home')


@login_required
@require_http_methods(['GET', 'POST'])
@fields_only('current_password', 'username', 'password1', 'password2')
def credentials(request):
    form = forms.CredentialsForm(request.POST if request.method == 'POST' else None,
                                 initial={'username': request.user.username})
    def success(_):
        logout(request)
        messages.success(request, 'Данные изменены. Все прежние сессии отозваны. Войдите заново.')
        return redirect('login')
    response = execute_form(request, form, lambda data: services.change_credentials(request, data), success)
    return response or form_page(request, form, 'Изменить логин / пароль', 400 if request.method == 'POST' else 200)


@login_required
@require_http_methods(['GET', 'POST'])
@fields_only('title', 'description', 'tags')
def topic_create(request):
    form = forms.TopicForm(request.POST if request.method == 'POST' else None)
    response = execute_form(request, form, lambda data: services.create_topic(request, data),
                            lambda topic: redirect('topic', pk=topic.pk))
    return response or form_page(request, form, 'Новое обсуждение', 400 if request.method == 'POST' else 200)


@login_required
@require_POST
@fields_only('text')
def message_create(request, pk):
    form = forms.MessageForm(request.POST)
    response = execute_form(request, form, lambda data: services.create_message(request, pk, data),
                            lambda _: redirect('topic', pk=pk))
    return response or form_page(request, form, 'Новое сообщение', 400)


@require_http_methods(['GET', 'POST'])
@fields_only('name')
def tags(request):
    form = forms.TagForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST':
        actor = services.current_actor(request)
        if not actor.is_moderator:
            raise PermissionDenied
        response = execute_form(request, form, lambda data: services.create_tag(request, data), lambda _: redirect('tags'))
        if response:
            return response
    items = list(Tag.objects.select_related('author'))
    for tag in items:
        tag.deletable = services.can_delete(request.user, tag)
    return render(request, 'forum/tags.html', {'tags': items, 'form': form},
                  status=400 if request.method == 'POST' else 200)


@login_required
@require_POST
@fields_only()
def delete(request, kind, pk):
    model = {'topic': Topic, 'message': Message, 'tag': Tag}[kind]
    try:
        services.delete_content(request, model, pk)
    except ValidationError as exc:
        return render(request, 'forum/error.html', {'errors': exc.messages}, status=409)
    return redirect('tags' if kind == 'tag' else 'search')
