import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('datasets', '0007_dataset_description'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProtectedRefRule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('branch', 'Branch'), ('tag', 'Tag')], max_length=6)),
                ('pattern', models.CharField(max_length=255)),
                ('minimum_role', models.CharField(choices=[('contributor', 'Contributor'), ('maintainer', 'Maintainer'), ('owner', 'Owner')], default='contributor', max_length=16)),
                ('deletion_minimum_role', models.CharField(choices=[('contributor', 'Contributor'), ('maintainer', 'Maintainer'), ('owner', 'Owner')], default='contributor', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('dataset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='protected_ref_rules', to='datasets.dataset')),
            ],
        ),
        migrations.AddConstraint(
            model_name='protectedrefrule',
            constraint=models.UniqueConstraint(fields=('dataset', 'kind', 'pattern'), name='unique_protected_ref_rule'),
        ),
    ]
