import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('datasets', '0009_browsercommitdraft_browsercommitchange'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('occurred_at', models.DateTimeField(auto_now_add=True, editable=False)),
                ('action', models.CharField(db_index=True, max_length=100)),
                ('outcome', models.CharField(choices=[('accepted', 'Accepted'), ('rejected', 'Rejected')], max_length=8)),
                ('reason_code', models.CharField(blank=True, max_length=100)),
                ('request_id', models.UUIDField(blank=True, editable=False, null=True)),
                ('actor_user_id', models.BigIntegerField(blank=True, editable=False, null=True)),
                ('actor_username', models.CharField(blank=True, editable=False, max_length=100)),
                ('access_token_id', models.UUIDField(blank=True, editable=False, null=True)),
                ('scope', models.CharField(choices=[('installation', 'Installation'), ('group', 'Group'), ('dataset', 'Dataset'), ('token', 'Access token')], max_length=12)),
                ('group_id', models.UUIDField(blank=True, editable=False, null=True)),
                ('group_path', models.CharField(blank=True, editable=False, max_length=2048)),
                ('dataset_id', models.UUIDField(blank=True, editable=False, null=True)),
                ('dataset_path', models.CharField(blank=True, editable=False, max_length=2048)),
                ('ref_name', models.CharField(blank=True, editable=False, max_length=1024)),
                ('token_id', models.UUIDField(blank=True, editable=False, null=True)),
                ('draft_id', models.UUIDField(blank=True, editable=False, null=True)),
                ('payload_version', models.PositiveSmallIntegerField(default=1, editable=False)),
                ('payload', models.JSONField(default=dict, editable=False)),
                ('deduplication_key', models.CharField(blank=True, editable=False, max_length=255, null=True, unique=True)),
            ],
            options={
                'ordering': ['-occurred_at', '-id'],
                'indexes': [
                    models.Index(fields=['dataset_id', '-occurred_at', '-id'], name='audit_dataset_time_idx'),
                    models.Index(fields=['group_id', '-occurred_at', '-id'], name='audit_group_time_idx'),
                    models.Index(fields=['actor_user_id', '-occurred_at', '-id'], name='audit_actor_time_idx'),
                ],
            },
        ),
    ]
