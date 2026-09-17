from django.db import migrations


def create_personal_namespaces(apps, schema_editor):
    """Create a personal namespace for every user predating namespace support."""

    User = apps.get_model('accounts', 'User')
    Namespace = apps.get_model('namespaces', 'Namespace')

    for user in User.objects.iterator():
        Namespace.objects.get_or_create(
            owner_user_id=user.pk,
            defaults={
                'kind': 'personal',
                'name': f'{user.first_name} {user.last_name}'.strip() or user.username,
                'slug': user.username.strip().lower(),
            },
        )


class Migration(migrations.Migration):
    """Backfill the personal-namespace invariant for existing users."""

    dependencies = [
        ('accounts', '0002_alter_user_username'),
        ('namespaces', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_personal_namespaces, migrations.RunPython.noop),
    ]
