from django.apps import AppConfig


class NetConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'net'

    def ready(self):
        from net.infrastructure import tzcheck  # noqa: F401  (registers the check)
