import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from datasets.lfs_cleanup import cleanup_lfs_orphans


class Command(BaseCommand):
    """Run bounded Git LFS orphan reconciliation as a worker or one-shot task."""

    help = 'Abort expired multipart uploads and delete Git LFS objects whose unreferenced grace period elapsed.'

    def add_arguments(self, parser):
        """Define bounded worker controls without introducing a queue service."""

        parser.add_argument('--once', action='store_true', help='Run one reconciliation pass and exit.')
        parser.add_argument('--interval', type=int, default=settings.NIYAN_LFS_MAINTENANCE_INTERVAL_SECONDS, help='Seconds between reconciliation passes.')
        parser.add_argument('--batch-size', type=int, default=100, help='Maximum sessions and objects considered per pass.')

    def handle(self, *args, **options):
        """Run reconciliation until stopped, or once when explicitly requested."""

        interval = options['interval']
        batch_size = options['batch_size']
        if interval < 1:
            raise CommandError('The maintenance interval must be positive.')
        if batch_size < 1 or batch_size > 1000:
            raise CommandError('The maintenance batch size must be between 1 and 1000.')

        while True:
            result = cleanup_lfs_orphans(batch_size=batch_size)
            self.stdout.write(
                f'expired_drafts={result.expired_drafts} aborted_multipart_uploads={result.aborted_multipart_uploads} deleted_objects={result.deleted_objects} failures={result.failures}'
            )
            if options['once']:
                return
            time.sleep(interval)
