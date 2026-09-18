from django.db import migrations, models


class Migration(migrations.Migration):
    """Add optional human-facing dataset summaries."""

    dependencies = [
        ('datasets', '0006_gitpushcontext_gitpushref_gitpushlfslease_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataset',
            name='description',
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
