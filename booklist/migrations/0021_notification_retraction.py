from django.db import migrations, models
from django.utils import timezone


def retract_inactive_recommendations(apps, schema_editor):
    alias = schema_editor.connection.alias
    Work = apps.get_model('booklist', 'Work')
    Event = apps.get_model('booklist', 'WorkLikeEvent')
    Note = apps.get_model('booklist', 'Notification')
    Public = apps.get_model('booklist', 'PublicRecommendation')
    Recommendation = apps.get_model('booklist', 'Recommendation')
    parents = dict(Work.objects.using(alias).values_list('pk', 'merged_into_id'))
    stale = []
    for event in Event.objects.using(alias).filter(kind='co_recommendation').select_related('actor').iterator():
        work_id = event.work_id
        seen = set()
        while parents.get(work_id) and work_id not in seen:
            seen.add(work_id)
            work_id = parents[work_id]
        common = dict(work_id=work_id, anonymous_client_id=event.actor.principal_id,
            withdrawn_at__isnull=True, invalidated_at__isnull=True, archived_at__isnull=True)
        if event.scope_key == 'public':
            active = Public.objects.using(alias).filter(**common).exists()
        elif event.scope_key.startswith('activity:') and event.scope_key[9:].isdecimal():
            active = Recommendation.objects.using(alias).filter(**common, scope='activity',
                activity_id=int(event.scope_key[9:]), activity_work__archived_at__isnull=True,
                activity_work__merged_into__isnull=True).exists()
        else:
            continue
        if not active:
            stale.append(event.pk)
    # Keep message IDs, read state, actors and dedup history intact.
    now = timezone.now()
    for start in range(0, len(stale), 500):
        Note.objects.using(alias).filter(event_id__in=stale[start:start+500],
            retracted_at__isnull=True).update(retracted_at=now)


class Migration(migrations.Migration):
    dependencies = [('booklist', '0020_recent_work_views')]
    operations = [
        migrations.AddField(model_name='notification', name='retracted_at', field=models.DateTimeField(null=True)),
        migrations.RunPython(retract_inactive_recommendations, migrations.RunPython.noop),
    ]
