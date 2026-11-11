"""FR-025 for FR-022: user-uploaded Sentinel-2 L2A products follow the Sentinel Data Legal Notice."""
from django.db import migrations

EV = ("User-uploaded Sentinel-2 L2A product: Copernicus Sentinel data under the Sentinel Data Legal Notice "
      "(licence register). The uploader is responsible for having obtained it lawfully.")


def seed(apps, schema_editor):
    SA = apps.get_model("projects", "SourceApproval")
    for use in ("internal_research", "evaluation", "commercial"):
        SA.objects.update_or_create(provider="upload", use=use, defaults={"approved": True, "licence_evidence": EV,
                                                                          "decided_by": "seeded from docs/licence_register.csv"})


class Migration(migrations.Migration):
    dependencies = [("projects", "0011_uploads")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
