from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('booklist','0019_activity_public_policy')]
    operations=[
        migrations.AddField(model_name='account',name='track_recent_views',field=models.BooleanField(default=True)),
        migrations.CreateModel(name='RecentWorkView',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('viewed_at',models.DateTimeField()),
            ('account',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to='booklist.account')),
            ('activity',models.ForeignKey(null=True,on_delete=django.db.models.deletion.PROTECT,to='booklist.activity')),
            ('work',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='booklist.work')),
        ],options={
            'constraints':[models.UniqueConstraint(fields=('account','work'),name='recent_view_account_work')],
            'indexes':[models.Index(fields=['account','-viewed_at','-id'],name='recent_view_account_time')],
        }),
    ]
