import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Persist creator-private browser commit drafts and their staged paths."""

    dependencies = [
        ('datasets', '0008_protectedrefrule'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BrowserCommitDraft',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('target_branch', models.CharField(max_length=255)),
                ('base_commit', models.CharField(blank=True, max_length=64)),
                ('state', models.CharField(choices=[('open', 'Open'), ('committed', 'Committed'), ('discarded', 'Discarded')], default='open', max_length=10)),
                ('committed_oid', models.CharField(blank=True, max_length=64)),
                ('expires_at', models.DateTimeField(editable=False)),
                ('committed_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('discarded_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='browser_commit_drafts', to=settings.AUTH_USER_MODEL)),
                ('dataset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='browser_commit_drafts', to='datasets.dataset')),
            ],
        ),
        migrations.CreateModel(
            name='BrowserCommitChange',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('path', models.CharField(max_length=4096)),
                ('operation', models.CharField(choices=[('upsert', 'Add or replace'), ('delete', 'Delete')], max_length=6)),
                ('storage', models.CharField(blank=True, choices=[('git', 'Git'), ('lfs', 'Git LFS')], max_length=3)),
                ('size', models.PositiveBigIntegerField(blank=True, null=True)),
                ('git_blob_oid', models.CharField(blank=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('draft', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='changes', to='datasets.browsercommitdraft')),
                ('lfs_object', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='browser_draft_changes', to='datasets.lfsobject')),
            ],
        ),
        migrations.AddConstraint(
            model_name='browsercommitdraft',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('committed_at__isnull', True), ('committed_oid', ''), ('discarded_at__isnull', True), ('state', 'open')), models.Q(('committed_at__isnull', False), ('committed_oid__gt', ''), ('discarded_at__isnull', True), ('state', 'committed')), models.Q(('committed_at__isnull', True), ('committed_oid', ''), ('discarded_at__isnull', False), ('state', 'discarded')), _connector='OR'), name='browser_draft_state_consistent'),
        ),
        migrations.AddConstraint(
            model_name='browsercommitchange',
            constraint=models.UniqueConstraint(fields=('draft', 'path'), name='unique_browser_draft_path'),
        ),
        migrations.AddConstraint(
            model_name='browsercommitchange',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('git_blob_oid', ''), ('lfs_object__isnull', True), ('operation', 'delete'), ('size__isnull', True), ('storage', '')), models.Q(('git_blob_oid__gt', ''), ('lfs_object__isnull', True), ('operation', 'upsert'), ('size__isnull', False), ('storage', 'git')), models.Q(('git_blob_oid', ''), ('lfs_object__isnull', False), ('operation', 'upsert'), ('size__isnull', False), ('storage', 'lfs')), _connector='OR'), name='browser_draft_change_storage_consistent'),
        ),
    ]
