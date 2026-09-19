from django.core.validators import RegexValidator


path_slug_validator = RegexValidator(
    regex=r'^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$',
    message='Use letters, numbers, periods, underscores, or hyphens; begin and end with a letter or number.',
)


def normalize_path_slug(value):
    """Normalize an identity used as a human-facing path component."""

    return value.strip().lower()


def _coverage_gate_probe():
    return False
