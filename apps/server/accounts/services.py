from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from datasets.audit import record_audit_event
from datasets.models import AuditEvent


def create_user(*, username, password, email='', first_name='', last_name='', created_by):
    """Create an active user and the user's personal namespace.

    Parameters
    ----------
    username : str
        Path-safe login name for the new account.
    password : str
        Initial password validated against the installation policy.
    email : str, optional
        Account email address.
    first_name : str, optional
        User's given name.
    last_name : str, optional
        User's family name.
    created_by : accounts.models.User
        Staff user performing the operation.

    Returns
    -------
    accounts.models.User
        Persisted active user with a personal namespace.
    """

    user_model = get_user_model()
    user = user_model(
        username=username,
        email=user_model.objects.normalize_email(email.strip()),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        is_active=True,
    )
    validate_password(password, user=user)
    user.set_password(password)
    user.full_clean()
    with transaction.atomic():
        user.save()
        record_audit_event(
            action='user.created',
            actor=created_by,
            scope=AuditEvent.Scope.INSTALLATION,
            payload={'created_user_id': user.pk, 'created_username': user.username},
        )
    return user
