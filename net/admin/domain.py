"""Admin registrations for immutable domain-operation and alert audit history."""

import re

from django import forms
from django.contrib import admin

from net.models import AlertChannel, AlertDelivery, AlertEvent, AlertPolicy, AlertState, AlertTestSend, DomainOperation
from net.secret_masks import MASKED_SECRET


_SENSITIVE_SETTINGS_KEY = re.compile(
    r'(secret|token|password|webhook|credential|api[_-]?key|private[_-]?key|authorization)',
    re.IGNORECASE,
)
_MISSING = object()


def _is_sensitive_settings_key(key):
    return isinstance(key, str) and bool(_SENSITIVE_SETTINGS_KEY.search(key))


def _safe_settings(value):
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            if _is_sensitive_settings_key(key):
                if item not in (None, '', [], {}):
                    safe[key] = MASKED_SECRET
            else:
                safe[key] = _safe_settings(item)
        return safe
    if isinstance(value, list):
        return [_safe_settings(item) for item in value]
    return value


def _merge_settings(existing, submitted):
    """Recursively preserve omitted dict secrets; lists are submitted atomically.

    A list may contain dictionaries whose sensitive entries were scrubbed from
    the displayed JSON.  Merging those list items with stored values could
    silently retain or reveal stale credentials, so callers must submit the
    complete replacement list and explicitly include any new secret values.
    """
    if isinstance(existing, dict) and isinstance(submitted, dict):
        merged = dict(existing)
        for key, value in submitted.items():
            previous = existing.get(key, _MISSING)
            if _is_sensitive_settings_key(key):
                if value not in (None, '', MASKED_SECRET):
                    merged[key] = value
            elif previous is _MISSING:
                merged[key] = value
            else:
                merged[key] = _merge_settings(previous, value)
        return merged
    return submitted


class AlertChannelAdminForm(forms.ModelForm):
    """Show a scrubbed settings JSON document and preserve hidden secrets."""

    class Meta:
        model = AlertChannel
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial['settings'] = _safe_settings(self.instance.settings or {})
            self.fields['settings'].required = False

    def clean_settings(self):
        submitted = self.cleaned_data.get('settings')
        if not (self.instance and self.instance.pk):
            return submitted
        existing = self.instance.settings or {}
        if submitted in (None, {}):
            return existing
        return _merge_settings(existing, submitted)


@admin.register(DomainOperation)
class DomainOperationAdmin(admin.ModelAdmin):
    list_display = ('id', 'action', 'object_type', 'requested_by', 'status', 'target_count', 'created_at', 'finished_at')
    list_filter = ('action', 'object_type', 'status')
    search_fields = ('id', 'requested_by__username', 'requested_by__email', 'task__id')
    readonly_fields = ('id', 'action', 'object_type', 'requested_by', 'status', 'target_count', 'parameter_summary', 'task', 'started_at', 'finished_at', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AlertChannel)
class AlertChannelAdmin(admin.ModelAdmin):
    form = AlertChannelAdminForm
    list_display = ('name', 'channel_type', 'is_enabled', 'created_at', 'updated_at')
    list_filter = ('channel_type', 'is_enabled')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'updated_at'


@admin.register(AlertPolicy)
class AlertPolicyAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_default', 'mode', 'inspection_profile', 'analysis_profile', 'updated_at')
    list_filter = ('is_default', 'mode')
    search_fields = ('name', 'inspection_profile__name', 'analysis_profile__name')
    raw_id_fields = ('inspection_profile', 'analysis_profile')
    filter_horizontal = ('channels',)
    readonly_fields = ('default_slot', 'created_at', 'updated_at')
    date_hierarchy = 'updated_at'


@admin.register(AlertState)
class AlertStateAdmin(admin.ModelAdmin):
    list_display = ('profile_type', 'profile_id', 'target_type', 'target_id', 'finding_key', 'status', 'last_seen_at')
    list_filter = ('status', 'profile_type', 'target_type')
    search_fields = ('profile_id', 'target_id', 'finding_key')
    readonly_fields = ('id', 'profile_type', 'profile_id', 'target_type', 'target_id', 'finding_key', 'status', 'finding_snapshot', 'last_seen_at', 'last_abnormal_at', 'last_normal_at', 'last_event', 'created_at', 'updated_at')
    date_hierarchy = 'last_seen_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'event_type', 'status', 'severity', 'target_type', 'target_id', 'occurred_at')
    list_filter = ('event_type', 'status', 'severity', 'profile_type', 'target_type')
    search_fields = ('id', 'profile_id', 'target_id', 'summary')
    readonly_fields = ('id', 'task', 'target_run', 'policy', 'profile_type', 'profile_id', 'target_type', 'target_id', 'event_type', 'status', 'severity', 'summary', 'findings', 'occurred_at', 'states', 'created_at', 'updated_at')
    date_hierarchy = 'occurred_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AlertDelivery)
class AlertDeliveryAdmin(admin.ModelAdmin):
    list_display = ('event', 'channel', 'status', 'attempt_count', 'next_attempt_at', 'delivered_at')
    list_filter = ('status',)
    search_fields = ('event__id', 'channel__name', 'response_summary', 'error_summary')
    readonly_fields = ('id', 'event', 'channel', 'status', 'attempt_count', 'max_attempts', 'next_attempt_at', 'attempted_at', 'delivered_at', 'lease_expires_at', 'lease_token', 'response_summary', 'error_summary', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AlertTestSend)
class AlertTestSendAdmin(admin.ModelAdmin):
    list_display = ('channel', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('channel__name', 'response_summary', 'error_summary')
    readonly_fields = ('id', 'channel', 'status', 'response_summary', 'error_summary', 'created_at')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
