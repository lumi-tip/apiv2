# Generated by Django 3.1.4 on 2021-01-19 03:26

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('authenticate', '0017_auto_20210113_0644'),
        ('feedback', '0011_cohortuserproxy'),
    ]

    operations = [
        migrations.AddField(
            model_name='answer',
            name='token',
            field=models.OneToOneField(blank=True, default=None, null=True, on_delete=django.db.models.deletion.SET_NULL, to='authenticate.token'),
        ),
    ]
