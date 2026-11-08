"""Append-only at the database level (FR-092): UPDATE and DELETE are refused even from raw SQL."""
from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION minefoot_refuse_change() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
""" + "".join(
    f"""
CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t}
  FOR EACH ROW EXECUTE FUNCTION minefoot_refuse_change();
""" for t in ['audit_auditevent'])

REVERSE = "".join(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t};" for t in ['audit_auditevent'])


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
