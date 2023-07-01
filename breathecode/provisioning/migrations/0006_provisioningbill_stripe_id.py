# Generated by Django 3.2.19 on 2023-07-01 01:08

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('provisioning', '0005_provisioningbill_stripe_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='provisioningbill',
            name='stripe_id',
            field=models.CharField(blank=True, default=None, help_text='Stripe id', max_length=32, null=True),
        ),
    ]
