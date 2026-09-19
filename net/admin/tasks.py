"""Admin registrations for task configuration, schedules, and task history."""

from django.contrib import admin
from django.utils.html import format_html_join

from net.models import (
    ComputerAnalysisProfile, InspectionProfile,
    PeopleSyncSource, Schedule, TaskRun, TaskTargetRun,
)
from net.secret_masks import MASKED_SECRET


@admin.register(InspectionProfile)
class InspectionProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'device_type', 'is_enabled', 'timeout_seconds', 'concurrent_workers', 'updated_at')
    list_filter = ('device_type', 'is_enabled', 'alert_policy_mode')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'updated_at'


@admin.register(ComputerAnalysisProfile)
class ComputerAnalysisProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_enabled', 'concurrent_workers', 'updated_at')
    list_filter = ('is_enabled', 'alert_policy_mode')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'updated_at'


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ('id', 'inspection_profile', 'analysis_profile', 'people_source', 'domain_config', 'kind', 'is_enabled', 'next_run_at', 'last_enqueued_at')
    list_filter = ('kind', 'is_enabled')
    search_fields = ('inspection_profile__name', 'analysis_profile__name', 'people_source__name', 'domain_config__name')
    raw_id_fields = ('inspection_profile', 'analysis_profile', 'people_source', 'domain_config')
    readonly_fields = ('next_run_at', 'last_enqueued_at', 'created_at', 'updated_at')
    date_hierarchy = 'next_run_at'


TASK_RUN_READONLY_FIELDS = ('id', 'task_type', 'source', 'inspection_profile', 'analysis_profile', 'schedule', 'people_source', 'people_applied_at', 'status', 'progress', 'available_at', 'started_at', 'finished_at', 'lease_expires_at', 'worker_id', 'attempt_count', 'error_summary', 'profile_snapshot', 'parameters_snapshot', 'selected_items_snapshot', 'target_scope_snapshot', 'scope_key', 'active_scope_key', 'total_targets', 'completed_targets', 'successful_targets', 'failed_targets', 'created_at', 'updated_at')


@admin.register(TaskRun)
class TaskRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'task_type', 'status', 'source', 'progress', 'total_targets', 'created_at', 'finished_at')
    list_filter = ('task_type', 'status', 'source')
    search_fields = ('id', 'scope_key', 'worker_id', 'error_summary')
    readonly_fields = TASK_RUN_READONLY_FIELDS
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


TASK_TARGET_READONLY_FIELDS = ('id', 'task', 'target_type', 'target_id', 'target_snapshot', 'execution_scope_key', 'status', 'attempt_count', 'started_at', 'finished_at', 'result_type', 'result_id', 'result_snapshot', 'error_message', 'alert_processed_at', 'alert_attempted_at', 'alert_processing_error', 'created_at', 'updated_at')


@admin.register(TaskTargetRun)
class TaskTargetRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'task', 'target_type', 'target_id', 'status', 'attempt_count', 'finished_at')
    list_filter = ('target_type', 'status')
    search_fields = ('id', 'target_id', 'execution_scope_key', 'error_message', 'result_id')
    readonly_fields = TASK_TARGET_READONLY_FIELDS
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PeopleSyncSource)
class PeopleSyncSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'source_type', 'source_key', 'is_enabled', 'last_tested_at', 'last_synced_at', 'updated_at')
    list_filter = ('source_type', 'is_enabled')
    search_fields = ('name', 'source_key')
    readonly_fields = ('credential_status', 'last_tested_at', 'last_synced_at', 'created_at', 'updated_at')
    date_hierarchy = 'updated_at'

    @admin.display(description='已保存凭据')
    def credential_status(self, obj):
        credentials = obj.credentials if obj else {}
        if not credentials:
            return '未保存'
        return format_html_join(
            '',
            '<span class="secret-mask-status">{}：{}</span><br>',
            ((name, MASKED_SECRET) for name in sorted(credentials)),
        )
