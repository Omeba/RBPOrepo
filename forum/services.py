"""All public content writes go through this transactional boundary."""
import json
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.password_validation import validate_password
from django.core import serializers
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.color import no_style
from django.db import connection, transaction
from django.db.models import F, Count
from django.http import Http404
from django.utils import timezone

from .models import User, Tag, Topic, Message, ContentLock, Revision


def lock_user(pk):
    # A write before reading also takes a write lock on SQLite, where FOR UPDATE
    # is not supported. PostgreSQL uses the same per-user row lock for login/change.
    if not User.objects.filter(pk=pk).update(credential_version=F('credential_version')):
        raise PermissionDenied
    return User.objects.select_for_update().get(pk=pk)


def current_actor(request):
    if not request.user.is_authenticated:
        raise PermissionDenied
    user = User.objects.get(pk=request.user.pk)
    if not user.is_active or request.session.get('credential_version') != user.credential_version:
        raise PermissionDenied('Сессия отозвана. Войдите заново.')
    return user


@transaction.atomic
def sign_in(request, username, password):
    if not User.objects.filter(username=username).update(credential_version=F('credential_version')):
        User().set_password(password)  # Same expensive hash path for unknown names.
        return False
    user = User.objects.select_for_update().get(username=username)
    if user.username != username or not user.is_active or not user.check_password(password):
        return False
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    request.session['credential_version'] = user.credential_version
    request.session.set_expiry(timezone.now() + timedelta(seconds=settings.SESSION_COOKIE_AGE))
    return True


@transaction.atomic
def change_credentials(request, data):
    user = lock_user(request.user.pk)
    if not user.is_active or request.session.get('credential_version') != user.credential_version:
        raise PermissionDenied('Сессия отозвана.')
    if not user.check_password(data['current_password']):
        raise ValidationError('Неверный текущий пароль.')
    if User.objects.exclude(pk=user.pk).filter(username=data['username']).exists():
        raise ValidationError('Этот логин уже занят.')
    if data['username'] == user.username and not data.get('password1'):
        raise ValidationError('Укажите новый логин или пароль.')
    user.username = data['username']
    if data.get('password1'):
        validate_password(data['password1'], user)
        user.set_password(data['password1'])
    user.credential_version += 1
    user.full_clean()
    user.save(update_fields=['username', 'password', 'credential_version'])


def snapshot():
    records = [*Tag.objects.order_by('pk'), *Topic.objects.order_by('pk'), *Message.objects.order_by('pk')]
    return json.loads(serializers.serialize('json', records))


@contextmanager
def content_change(request, operation):
    with transaction.atomic():
        if not ContentLock.objects.filter(pk=1).update(revision=F('revision') + 1):
            raise RuntimeError('Run migrations: missing content lock')
        actor = current_actor(request) if request is not None else None
        before = snapshot()
        yield actor
        Revision.objects.create(actor=actor, operation=operation, before=before, after=snapshot())


def get_object(model, pk):
    try:
        return model.objects.select_related('author').get(pk=pk)
    except model.DoesNotExist:
        raise Http404


def can_delete(user, obj):
    if not user.is_authenticated:
        return False
    if isinstance(obj, Tag):
        return user.is_moderator and obj.author_id == user.pk
    return obj.author_id == user.pk or (user.is_moderator and not obj.author.is_moderator)


def create_topic(request, data):
    with content_change(request, 'topic.create') as actor:
        ids = [tag.pk for tag in data['tags']]
        tags = list(Tag.objects.filter(pk__in=ids))
        if not tags or len(tags) != len(set(ids)):
            raise ValidationError('Выберите хотя бы один существующий тег. Обновите форму.')
        topic = Topic.objects.create(author=actor, title=data['title'], description=data['description'])
        topic.tags.set(tags)
    return topic


def create_message(request, topic_id, data):
    with content_change(request, 'message.create') as actor:
        topic = get_object(Topic, topic_id)
        message = Message.objects.create(topic=topic, author=actor, text=data['text'])
    return message


def create_tag(request, data):
    with content_change(request, 'tag.create') as actor:
        if not actor.is_moderator:
            raise PermissionDenied
        tag = Tag.objects.create(author=actor, name=data['name'])
    return tag


def delete_content(request, model, pk):
    with content_change(request, f'{model.__name__.lower()}.delete') as actor:
        obj = get_object(model, pk)
        if not can_delete(actor, obj):
            raise PermissionDenied('Нет прав на удаление этого объекта.')
        if isinstance(obj, Tag):
            affected = list(obj.topics.values_list('pk', flat=True))
            if Topic.objects.filter(pk__in=affected).annotate(n=Count('tags')).filter(n__lte=1).exists():
                raise ValidationError('Тег нельзя удалить: он является последним у обсуждения.')
        if isinstance(obj, Topic):
            if obj.messages.filter(author__is_moderator=True).exclude(author=actor).exists():
                raise ValidationError('Нельзя каскадно удалить сообщения другого модератора.')
        obj.delete()


def restore_revision(revision_id, side='before'):
    """Operator-only command. Restores content, never users, passwords or sessions."""
    if side not in ('before', 'after'):
        raise ValidationError('Unknown snapshot side')
    with content_change(None, f'restore.{revision_id}.{side}'):
        revision = Revision.objects.get(pk=revision_id)
        records = getattr(revision, side)
        # PROTECT keeps authors alive. All deletions and reconstruction are atomic.
        Topic.objects.all().delete()
        Tag.objects.all().delete()
        for obj in serializers.deserialize('json', json.dumps(records)):
            obj.save()
        if Topic.objects.annotate(n=Count('tags')).filter(n=0).exists():
            raise ValidationError('Invalid history: topic without tags')
        with connection.cursor() as cursor:
            for sql in connection.ops.sequence_reset_sql(no_style(), [Tag, Topic, Message]):
                cursor.execute(sql)
