# backend/dashboard/management/commands/archive_old_gate_logs.py
import csv
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from dashboard.models import GateLog


class Command(BaseCommand):
    help = "Exports gate logs older than N days to CSV, then deletes them. Never deletes without exporting first."

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=730, help='Age threshold in days (default 730 = 2 years)')

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options['days'])
        old_logs = GateLog.objects.filter(status='exited', exit_time__lt=cutoff)
        count = old_logs.count()

        if count == 0:
            self.stdout.write("No logs old enough to archive.")
            return

        filename = f"/app/gate_logs_archive_{timezone.now().date()}.csv"
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Guest', 'Plate', 'Entry', 'Exit', 'Logged In By', 'Logged Out By'])
            for log in old_logs:
                writer.writerow([
                    log.guest_name, log.plate_number, log.entry_time, log.exit_time,
                    log.logged_in_by.username, log.logged_out_by.username if log.logged_out_by else '',
                ])

        old_logs.delete()
        self.stdout.write(self.style.SUCCESS(f"Archived {count} logs to {filename} and removed them from the live table."))

####docker compose exec web python manage.py archive_old_gate_logs command to archive old gate logs