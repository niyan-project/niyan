import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_alter_user_username'),
        ('datasets', '0002_dataset_deletion_started_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeviceAuthorization',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('device_secret_digest', models.CharField(editable=False, max_length=64)),
                ('user_code', models.CharField(editable=False, max_length=9, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('requested_scopes', models.JSONField(default=list)),
                ('requested_dataset_path', models.CharField(blank=True, max_length=255)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('denied', 'Denied'), ('consumed', 'Consumed')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('last_polled_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('decided_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('consumed_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='device_authorizations', to=settings.AUTH_USER_MODEL)),
                ('dataset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='device_authorizations', to='datasets.dataset')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='AccessToken',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('origin', models.CharField(choices=[('manual', 'Manual'), ('cli', 'CLI')], max_length=10)),
                ('resource_boundary', models.CharField(choices=[('user', 'User'), ('dataset', 'Dataset')], max_length=10)),
                ('scopes', models.JSONField(default=list)),
                ('selector', models.CharField(editable=False, max_length=16, unique=True)),
                ('secret_digest', models.CharField(editable=False, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_used_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('expires_at', models.DateTimeField()),
                ('revoked_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('dataset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='access_tokens', to='datasets.dataset')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='access_tokens', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(models.Q(('dataset__isnull', True), ('resource_boundary', 'user')), models.Q(('dataset__isnull', False), ('resource_boundary', 'dataset')), _connector='OR'), name='access_token_resource_boundary_matches_dataset')],
            },
        ),
    ]
