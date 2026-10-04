from django.core.management.base import BaseCommand, CommandError
from forum.models import Revision
from forum.services import restore_revision


class Command(BaseCommand):
    help = 'Restore ALL forum content to a revision. Prior current state is preserved in a new revision.'

    def add_arguments(self, parser):
        parser.add_argument('revision_id', type=int)
        parser.add_argument('--side', choices=['before', 'after'], default='before')
        parser.add_argument('--confirm', action='store_true')

    def handle(self, *args, **options):
        if not options['confirm']:
            raise CommandError('Full content rollback requires --confirm. Inspect content_history first.')
        try:
            restore_revision(options['revision_id'], options['side'])
        except Revision.DoesNotExist:
            raise CommandError('Revision not found')
        self.stdout.write(self.style.SUCCESS('Содержимое восстановлено; прежнее состояние сохранено в новой ревизии.'))
