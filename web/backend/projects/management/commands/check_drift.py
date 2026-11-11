"""FR-105: run the drift checks for every organisation and record each alert in the audit trail. Schedule it daily."""
from django.core.management.base import BaseCommand

from accounts.models import Organisation
from audit.models import record


class Command(BaseCommand):
    help = __doc__

    def handle(self, **_):
        from projects.monitoring import report
        n = 0
        for org in Organisation.objects.all():
            for a in report(org)["alerts"]:
                record(None, "monitoring.alert", None, after=a, organisation=org)
                self.stdout.write(f"{org.name}: {a['area']} {a['season']} {a['kind']}: {a['message']}")
                n += 1
        self.stdout.write(f"{n} alert(s)")
