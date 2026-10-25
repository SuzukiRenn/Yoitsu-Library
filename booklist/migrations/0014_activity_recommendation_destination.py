from django.db import migrations, models
from django.db.models import Min


def preserve_participation_history(apps, schema_editor):
    Activity = apps.get_model('booklist', 'Activity')
    Recommendation = apps.get_model('booklist', 'Recommendation')
    alias = schema_editor.connection.alias
    # All past participation counts, including withdrawn/invalidated/archived rows.
    for row in Recommendation.objects.using(alias).filter(scope='activity', activity_id__isnull=False).values('activity_id').annotate(first=Min('created_at')):
        Activity.objects.using(alias).filter(pk=row['activity_id'], first_recommended_at__isnull=True).update(first_recommended_at=row['first'])


class Migration(migrations.Migration):
    dependencies = [('booklist', '0013_reason_references')]
    # Add columns in place: rebuilding either table would drop its audit/integrity triggers.
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[
            migrations.RunSQL('ALTER TABLE booklist_activity ADD COLUMN first_recommended_at datetime NULL',
                              'ALTER TABLE booklist_activity DROP COLUMN first_recommended_at'),
            migrations.RunSQL("ALTER TABLE booklist_activity ADD COLUMN recommendation_destination varchar(20) NOT NULL DEFAULT 'public' CONSTRAINT activity_recommendation_destination CHECK (recommendation_destination IN ('public', 'activity_only'))",
                              'ALTER TABLE booklist_activity DROP COLUMN recommendation_destination'),
            migrations.RunSQL("ALTER TABLE booklist_pendingactivitysubmission ADD COLUMN recommendation_destination varchar(20) NOT NULL DEFAULT 'public'",
                              'ALTER TABLE booklist_pendingactivitysubmission DROP COLUMN recommendation_destination'),
        ],
        state_operations=[
            migrations.AddField(model_name='activity', name='first_recommended_at', field=models.DateTimeField(null=True)),
            migrations.AddField(model_name='activity', name='recommendation_destination', field=models.CharField(choices=[('public', '同时推荐到公共书架'), ('activity_only', '仅推荐到本期活动')], default='public', max_length=20)),
            migrations.AddField(model_name='pendingactivitysubmission', name='recommendation_destination', field=models.CharField(default='public', max_length=20)),
            migrations.AddConstraint(model_name='activity', constraint=models.CheckConstraint(condition=models.Q(recommendation_destination__in=['public', 'activity_only']), name='activity_recommendation_destination')),
        ],
    ), migrations.RunPython(preserve_participation_history, migrations.RunPython.noop)]
