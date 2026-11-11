"""Seed FR-025 approvals from docs/licence_register.csv as read on 2026-11-03.
Planetary Computer's service terms allow evaluation and testing only, so it is not approved for commercial use."""
from django.db import migrations

PC = "Planetary Computer service terms: Preview, 'solely for evaluation and testing' (licence register, read 2026-11-03)"
ES = "Earth Search / AWS Open Data: Sentinel Data Legal Notice, free, full and open (licence register, read 2026-11-03)"
CD = "Copernicus Data Space: Sentinel data under the Sentinel Data Legal Notice (licence register, read 2026-11-03)"
ROWS = [("planetary", "internal_research", True, PC), ("planetary", "evaluation", True, PC), ("planetary", "commercial", False, PC),
        ("earth-search", "internal_research", True, ES), ("earth-search", "evaluation", True, ES), ("earth-search", "commercial", True, ES),
        ("cdse", "internal_research", True, CD), ("cdse", "evaluation", True, CD), ("cdse", "commercial", True, CD)]


def seed(apps, schema_editor):
    SA = apps.get_model("projects", "SourceApproval")
    for p, u, ok, ev in ROWS:
        SA.objects.update_or_create(provider=p, use=u, defaults={"approved": ok, "licence_evidence": ev,
                                                                 "decided_by": "seeded from docs/licence_register.csv"})


class Migration(migrations.Migration):
    dependencies = [("projects", "0007_intended_use_source_approvals")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
