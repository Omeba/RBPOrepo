from django.core.management.base import BaseCommand
from forum.models import Revision


class Command(BaseCommand):
    help = 'List persistent content revisions (no passwords/session data).'

    def handle(self, *args, **options):
        for item in Revision.objects.order_by('-pk')[:100]:
            self.stdout.write(f'{item.pk}\t{item.created_at.isoformat()}\t{item.actor_id}\t{item.operation}')
