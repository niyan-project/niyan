from django.contrib.auth.models import AbstractUser
from django.db import models

from accounts.validators import normalize_path_slug, path_slug_validator


class User(AbstractUser):
    """Represent a Niyān user with Django's standard authentication fields."""

    username = models.CharField(
        max_length=100,
        unique=True,
        validators=[path_slug_validator],
        error_messages={'unique': 'A user with that username already exists.'},
        help_text='Used for login and the initial personal namespace path.',
    )

    def clean(self):
        """Normalize the username before uniqueness validation."""

        super().clean()
        self.username = normalize_path_slug(self.username)

    def save(self, *args, **kwargs):
        """Persist the user with a normalized path-safe username.

        Parameters
        ----------
        *args
            Positional arguments passed to Django's model save operation.
        **kwargs
            Keyword arguments passed to Django's model save operation.
        """

        self.username = normalize_path_slug(self.username)
        path_slug_validator(self.username)
        return super().save(*args, **kwargs)
