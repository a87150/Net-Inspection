"""Django admin entry point with domain-focused registrations."""

from . import assets, domain, records, tasks  # noqa: F401
from django.contrib import admin
from net.models import IssueSeverityPolicy
admin.site.register(IssueSeverityPolicy)
