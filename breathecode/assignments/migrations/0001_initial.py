# Generated by Django 3.0.7 on 2020-06-16 16:52

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Task',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('associated_slug', models.CharField(max_length=150, unique=True)),
                ('title', models.CharField(max_length=150)),
                ('task_status', models.CharField(choices=[('PENDING', 'Pending'), ('DONE', 'Done')], default='PENDING', max_length=15)),
                ('revision_status', models.CharField(choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')], max_length=15)),
                ('task_type', models.CharField(choices=[('PROJECT', 'project'), ('QUIZ', 'quiz'), ('LESSON', 'lesson'), ('REPLIT', 'replit')], max_length=15)),
                ('github_url', models.CharField(default=True, max_length=150, null=True)),
                ('live_url', models.CharField(default=True, max_length=150, null=True)),
                ('description', models.TextField(max_length=450)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
