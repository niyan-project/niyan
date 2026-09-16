from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Represent a Niyān user with Django's standard authentication fields."""
