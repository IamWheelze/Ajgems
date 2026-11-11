"""Fill Tile.score_path for jobs run before it existed: the score map sits in the job folder as <grid_id>_score.tif."""
from pathlib import Path

from django.db import migrations


def backfill(apps, schema_editor):
    Tile = apps.get_model("projects", "Tile")
    for t in Tile.objects.filter(score_path="").exclude(prepared_path=""):
        p = Path(t.prepared_path).parents[1] / f"{t.grid_id}_score.tif"
        if p.exists():
            t.score_path = str(p)
            t.save(update_fields=["score_path"])


class Migration(migrations.Migration):
    dependencies = [("projects", "0004_tile_score_path")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
