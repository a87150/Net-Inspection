from django.apps import AppConfig


class WeakCurrentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "weakcurrent"
    verbose_name = "弱电设备"
