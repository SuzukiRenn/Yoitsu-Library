from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('booklist','0012_reason_spoilers')]
    operations=[migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunSQL(
            f"ALTER TABLE booklist_{name} ADD COLUMN reason_references text NOT NULL DEFAULT '[]' CHECK (JSON_VALID(reason_references))",
            f'ALTER TABLE booklist_{name} DROP COLUMN reason_references',
        ) for name in ('recommendation','pendingactivitysubmission')],
        state_operations=[migrations.AddField(model_name=name,name='reason_references',field=models.JSONField(default=list))
                          for name in ('recommendation','pendingactivitysubmission')],
    )]
