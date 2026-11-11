"""FR-095: field visits and photos are append-only at the database level."""
from django.db import migrations

TABLES = ["review_fieldvisit", "review_fieldphoto"]


class Migration(migrations.Migration):
    dependencies = [("review", "0007_field_visits")]
    operations = [migrations.RunSQL(
        "".join(f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION minefoot_refuse_change();" for t in TABLES),
        "".join(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t};" for t in TABLES))]
