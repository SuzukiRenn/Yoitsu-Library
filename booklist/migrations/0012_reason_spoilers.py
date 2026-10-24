from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('booklist', '0011_feedback')]
    # SQLite table rebuilding would drop the existing contribution/audit triggers.
    # Add a constant-default column in place, preserving all rows and triggers.
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunSQL(
            f'ALTER TABLE booklist_{name} ADD COLUMN contains_spoilers bool NOT NULL DEFAULT 0',
            f'ALTER TABLE booklist_{name} DROP COLUMN contains_spoilers',
        ) for name in ('recommendation', 'pendingactivitysubmission')],
        state_operations=[migrations.AddField(
            model_name=name, name='contains_spoilers', field=models.BooleanField(default=False),
        ) for name in ('recommendation', 'pendingactivitysubmission')],
    )]
