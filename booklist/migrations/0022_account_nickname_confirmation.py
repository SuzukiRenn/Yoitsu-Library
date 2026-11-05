from django.db import migrations, models
import re

def backfill(apps,schema_editor):
    Account=apps.get_model('booklist','Account')
    accounts=Account.objects.using(schema_editor.connection.alias).filter(nickname_confirmed_at__isnull=True)
    for account in accounts.only('id','username','display_name','password_hash','qq_email','email_verified_at','created_at','updated_at').iterator(chunk_size=500):
        generated=(account.qq_email and account.email_verified_at
                   and re.fullmatch(r'mail_[0-9a-f]{24}',account.username)
                   and account.password_hash.startswith('!')
                   and re.fullmatch(r'读者[0-9a-f]{6}',account.display_name))
        if not generated:
            accounts.filter(pk=account.pk).update(nickname_confirmed_at=account.updated_at or account.created_at)

class Migration(migrations.Migration):
    dependencies=[('booklist','0021_notification_retraction')]
    operations=[migrations.AddField(model_name='account',name='nickname_confirmed_at',field=models.DateTimeField(null=True)),
                migrations.RunPython(backfill,migrations.RunPython.noop)]
