from django.core.exceptions import ValidationError
from django.db import IntegrityError

from accounts.validators import normalize_path_slug
from namespaces.models import Namespace


def ensure_personal_namespace(user):
    """Return the one personal namespace belonging to a user, creating it when needed.

    Parameters
    ----------
    user : accounts.models.User
        Persisted Niyān user whose namespace is required.

    Returns
    -------
    namespaces.models.Namespace
        Existing or newly created personal namespace.

    Raises
    ------
    ValidationError
        If the user's normalized username conflicts with another root path.
    """

    existing_namespace = Namespace.objects.filter(owner_user=user).first()
    if existing_namespace is not None:
        return existing_namespace

    slug = normalize_path_slug(user.get_username())
    display_name = user.get_full_name().strip() or user.get_username()
    namespace = Namespace(kind=Namespace.Kind.PERSONAL, name=display_name, slug=slug, owner_user=user)
    namespace.full_clean()

    try:
        namespace.save()
    except IntegrityError as error:
        raise ValidationError({'username': 'This username conflicts with an existing root namespace path.'}) from error

    return namespace
