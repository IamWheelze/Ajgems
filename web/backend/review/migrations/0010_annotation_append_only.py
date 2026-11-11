from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("review", "0009_annotation")]
    operations = [migrations.RunSQL(
        "CREATE TRIGGER review_annotationversion_append_only BEFORE UPDATE OR DELETE ON review_annotationversion "
        "FOR EACH ROW EXECUTE FUNCTION minefoot_refuse_change();",
        "DROP TRIGGER IF EXISTS review_annotationversion_append_only ON review_annotationversion;")]
