# Generated by Django 5.1.1 on 2024-09-24 21:17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("marketing", "0087_alter_formentry_storage_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="formentry",
            name="storage_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("PERSISTED", "Persisted"),
                    ("REJECTED", "Rejected"),
                    ("DUPLICATED", "Duplicated"),
                    ("ERROR", "Error"),
                ],
                default="PENDING",
                help_text="MANUALLY_PERSISTED means it was copy pasted into active campaign",
                max_length=20,
            ),
        ),
    ]