# Generated by Django 3.2.16 on 2023-01-20 06:13

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0012_auto_20230115_0509'),
    ]

    operations = [
        migrations.AddField(
            model_name='currency',
            name='decimals',
            field=models.IntegerField(default=0),
        ),
    ]