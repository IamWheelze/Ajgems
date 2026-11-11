"""FR-093: missed sites are append-only at the database level, like reviews."""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("review", "0003_missed_sites")]
    operations = [migrations.RunSQL(
        "CREATE TRIGGER review_missedsite_append_only BEFORE UPDATE OR DELETE ON review_missedsite "
        "FOR EACH ROW EXECUTE FUNCTION minefoot_refuse_change();",
        "DROP TRIGGER IF EXISTS review_missedsite_append_only ON review_missedsite;")]
