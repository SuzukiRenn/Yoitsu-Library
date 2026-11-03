from django.db import migrations, models
from django.db.models import Min


def preserve_policy(apps, schema_editor):
    Activity=apps.get_model('booklist','Activity')
    # Include withdrawn/archived contributions: withdrawing or moderating
    # the first effective participation must never unlock an established rule.
    alias=schema_editor.connection.alias
    rows=apps.get_model('booklist','Recommendation').objects.using(alias).filter(scope='activity')
    for row in rows.values('activity_id').annotate(first=Min('created_at')):
        if row['activity_id'] and row['first']:
            Activity.objects.using(alias).filter(pk=row['activity_id']).update(participation_locked_at=row['first'])


class Migration(migrations.Migration):
    dependencies=[('booklist','0018_imported_attribution')]
    operations=[
        migrations.AddField(model_name='activity',name='sync_public',field=models.BooleanField(default=True)),
        migrations.AddField(model_name='activity',name='participation_locked_at',field=models.DateTimeField(null=True)),
        migrations.AddField(model_name='pendingactivitysubmission',name='sync_public',field=models.BooleanField(default=True)),
        migrations.RunPython(preserve_policy,migrations.RunPython.noop),
    ]
