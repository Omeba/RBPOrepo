from types import SimpleNamespace
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from forum.models import User, Tag
from forum.services import lock_user, create_tag


class Command(BaseCommand):
    help = 'Grant an existing user administrator and moderator roles; revoke old sessions.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--seed-tags', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        pk = User.objects.filter(username=options['username'], is_active=True).values_list('pk', flat=True).first()
        if pk is None:
            raise CommandError('Существующий активный пользователь не найден.')
        user = lock_user(pk)
        old_password = user.password
        user.is_staff = user.is_superuser = user.is_moderator = True
        user.credential_version += 1
        user.save(update_fields=['is_staff', 'is_superuser', 'is_moderator', 'credential_version'])
        count = 0
        if options['seed_tags'] and not Tag.objects.exists():
            request = SimpleNamespace(user=user, session={'credential_version': user.credential_version})
            for name in ('Искусственный интеллект', 'Машинное обучение', 'Нейросети', 'LLM', 'Безопасность ИИ'):
                create_tag(request, {'name': name})
                count += 1
        user.refresh_from_db()
        if user.password != old_password:
            raise CommandError('Unexpected password change; transaction rolled back.')
        self.stdout.write(self.style.SUCCESS(
            f'{user.username}: администратор и модератор; пароль сохранён; сессии отозваны; создано тегов: {count}.'))
