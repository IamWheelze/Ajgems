from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("review", "0011_corrections")]
    operations = [migrations.RunSQL(
        "CREATE TRIGGER review_correctionevent_append_only BEFORE UPDATE OR DELETE ON review_correctionevent "
        "FOR EACH ROW EXECUTE FUNCTION minefoot_refuse_change();",
        "DROP TRIGGER IF EXISTS review_correctionevent_append_only ON review_correctionevent;")]
