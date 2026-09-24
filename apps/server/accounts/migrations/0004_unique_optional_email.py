from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower


def normalize_existing_emails(apps, schema_editor):
    """Normalize existing addresses and refuse ambiguous duplicates."""

    User = apps.get_model('accounts', 'User')
    seen = set()
    for user in User.objects.exclude(email='').order_by('id'):
        normalized = user.email.strip().lower()
        if normalized in seen:
            raise RuntimeError(f'Users contain duplicate email address {normalized!r}; resolve it before applying this migration.')
        seen.add(normalized)
        if user.email != normalized:
            User.objects.filter(pk=user.pk).update(email=normalized)


class Migration(migrations.Migration):
    dependencies = [('accounts', '0003_deviceauthorization_accesstoken')]

    operations = [
        migrations.AlterField(model_name='user', name='email', field=models.EmailField(blank=True, max_length=254)),
        migrations.RunPython(normalize_existing_emails, migrations.RunPython.noop),
        migrations.AddConstraint(model_name='user', constraint=models.UniqueConstraint(Lower('email'), condition=~Q(email=''), name='unique_nonblank_user_email_ci')),
    ]
