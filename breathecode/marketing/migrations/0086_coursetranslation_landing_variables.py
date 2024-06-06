# Generated by Django 5.0.4 on 2024-05-06 15:23

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0085_merge_20240403_0116'),
    ]

    operations = [
        migrations.AddField(
            model_name='coursetranslation',
            name='landing_variables',
            field=models.JSONField(
                blank=True,
                default=None,
                help_text='Different variables that can be used for marketing purposes in the landing page.',
                null=True),
        ),
    ]