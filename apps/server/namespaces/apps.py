from django.apps import AppConfig


class NamespacesConfig(AppConfig):
    """Configure the Niyān namespaces application."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'namespaces'

    def ready(self):
        """Register user lifecycle hooks after Django loads application models."""

        import namespaces.signals  # noqa: F401
