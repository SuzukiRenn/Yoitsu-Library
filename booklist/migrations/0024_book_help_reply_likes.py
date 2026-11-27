import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('booklist', '0023_course_features')]

    operations = [
        migrations.AddField(
            model_name='bookquestion', name='deleted_at', field=models.DateTimeField(null=True),
        ),
        migrations.CreateModel(
            name='BookCandidateLike',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('cancelled_at', models.DateTimeField(null=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='booklist.account')),
                ('candidate', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                related_name='likes', to='booklist.bookcandidate')),
            ],
            options={'constraints': [
                models.UniqueConstraint(fields=('candidate', 'actor'), name='course_candidate_like_unique'),
            ]},
        ),
        migrations.AlterField(
            model_name='worklikeevent', name='kind',
            field=models.CharField(choices=[('work_like', '作品点赞'), ('reason_like', '理由赞同'),
                ('co_recommendation', '共同推荐'), ('reply_like', '求助回复点赞')], default='work_like', max_length=20),
        ),
    ]
