import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('datasets', '0002_dataset_deletion_started_at'),
        ('namespaces', '0002_create_personal_namespaces'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DatasetGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('owner', 'Owner'), ('maintainer', 'Maintainer'), ('contributor', 'Contributor'), ('reader', 'Reader')], max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('dataset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='grants', to='datasets.dataset')),
                ('group_namespace', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='dataset_grants', to='namespaces.namespace')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='dataset_grants', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(models.Q(('group_namespace__isnull', True), ('user__isnull', False)), models.Q(('group_namespace__isnull', False), ('user__isnull', True)), _connector='OR'), name='dataset_grant_has_one_principal'), models.UniqueConstraint(condition=models.Q(('user__isnull', False)), fields=('dataset', 'user'), name='unique_dataset_user_grant'), models.UniqueConstraint(condition=models.Q(('group_namespace__isnull', False)), fields=('dataset', 'group_namespace'), name='unique_dataset_group_grant')],
            },
        ),
    ]
