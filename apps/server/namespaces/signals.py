from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from namespaces.services import ensure_personal_namespace


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid='namespaces.ensure_personal_namespace')
def create_personal_namespace_for_user(sender, instance, created, raw, **kwargs):
    """Create the personal namespace required by every newly created user.

    Parameters
    ----------
    sender : type
        Configured Django user model.
    instance : accounts.models.User
        User instance emitted by Django.
    created : bool
        Whether this save inserted a new user.
    raw : bool
        Whether Django is loading raw fixture data.
    **kwargs
        Additional signal metadata supplied by Django.
    """

    if created and not raw:
        ensure_personal_namespace(instance)
