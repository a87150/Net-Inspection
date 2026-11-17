"""Django admin entry point with domain-focused registrations."""

from . import alerts, assets, domain, ipam, records, tasks  # noqa: F401
from django.contrib import admin
from net.models import IssueSeverityPolicy
admin.site.register(IssueSeverityPolicy)
