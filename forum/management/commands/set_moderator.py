from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from forum.models import User
from forum.services import lock_user


class Command(BaseCommand):
    help = 'Grant/revoke moderator role through trusted operator access; invalidate all sessions.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--revoke', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        pk = User.objects.filter(username=options['username']).values_list('pk', flat=True).first()
        if pk is None:
            raise CommandError('Пользователь не найден. Сначала зарегистрируйте его.')
        user = lock_user(pk)
        user.is_moderator = not options['revoke']
        user.credential_version += 1
        user.save(update_fields=['is_moderator', 'credential_version'])
        self.stdout.write(self.style.SUCCESS('Роль изменена, прежние сессии отозваны.'))
