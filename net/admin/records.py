"""Admin registrations for generated evidence records and errors."""

from django.contrib import admin

from net.models import (
    ComputerAnalysis, ComputerLogFile,
    Error_Computer, Error_Monitor, Error_Network_Device, Error_Server,
    Monitor_Inspection, Network_Device_Inspection, Server_Inspection,
)


class GeneratedRecordAdmin(admin.ModelAdmin):
    readonly_fields = ('id', 'status', 'started_at', 'finished_at', 'summary', 'details', 'created_at', 'task_target')
    list_filter = ('status',)
    date_hierarchy = 'created_at'


@admin.register(ComputerLogFile)
class ComputerLogFileAdmin(admin.ModelAdmin):
    list_display = ('content_hash', 'computer', 'collected_date', 'platform', 'retained', 'created_at')
    list_filter = ('retained', 'platform')
    search_fields = ('content_hash', 'computer__computer_name')
    readonly_fields = tuple(field.name for field in ComputerLogFile._meta.fields)
    raw_id_fields = ('computer',)
    date_hierarchy = 'created_at'


@admin.register(ComputerAnalysis)
class ComputerAnalysisAdmin(GeneratedRecordAdmin):
    list_display = ('computer', 'status', 'started_at', 'finished_at', 'created_at')
    search_fields = ('computer__computer_name', 'summary')
    raw_id_fields = ('computer', 'task_target')
    readonly_fields = GeneratedRecordAdmin.readonly_fields + ('computer', 'log_id', 'source_collected_at', 'analysis_items', 'exceptions')


class InfrastructureRecordAdmin(GeneratedRecordAdmin):
    search_fields = ('summary',)
    readonly_fields = GeneratedRecordAdmin.readonly_fields + ('is_reachable', 'duration_ms', 'raw_output')
    raw_id_fields = ('task_target',)


@admin.register(Network_Device_Inspection)
class NetworkDeviceInspectionAdmin(InfrastructureRecordAdmin):
    list_display = ('device', 'status', 'is_reachable', 'duration_ms', 'finished_at', 'created_at')
    search_fields = ('device__device_name', 'device__ip', 'summary')
    raw_id_fields = ('device', 'task_target')
    readonly_fields = InfrastructureRecordAdmin.readonly_fields + ('device',)


@admin.register(Server_Inspection)
class ServerInspectionAdmin(InfrastructureRecordAdmin):
    list_display = ('server', 'status', 'is_reachable', 'duration_ms', 'finished_at', 'created_at')
    search_fields = ('server__name', 'server__ip', 'summary')
    raw_id_fields = ('server', 'task_target')
    readonly_fields = InfrastructureRecordAdmin.readonly_fields + ('server',)


@admin.register(Monitor_Inspection)
class MonitorInspectionAdmin(InfrastructureRecordAdmin):
    list_display = ('monitor', 'status', 'is_reachable', 'duration_ms', 'finished_at', 'created_at')
    search_fields = ('monitor__device_name', 'monitor__ip', 'summary')
    raw_id_fields = ('monitor', 'task_target')
    readonly_fields = InfrastructureRecordAdmin.readonly_fields + ('monitor',)


class GeneratedErrorAdmin(admin.ModelAdmin):
    readonly_fields = ('id', 'inspection', 'error_message')
    search_fields = ('error_message',)


@admin.register(Error_Computer)
class ComputerErrorAdmin(GeneratedErrorAdmin):
    list_display = ('inspection', 'error_type', 'error_message')
    readonly_fields = GeneratedErrorAdmin.readonly_fields + ('error_type',)
    search_fields = ('error_type', 'error_message')
    raw_id_fields = ('inspection',)


@admin.register(Error_Network_Device)
class NetworkDeviceErrorAdmin(GeneratedErrorAdmin):
    list_display = ('inspection', 'error_message')
    raw_id_fields = ('inspection',)


@admin.register(Error_Server)
class ServerErrorAdmin(GeneratedErrorAdmin):
    list_display = ('inspection', 'error_message')
    raw_id_fields = ('inspection',)


@admin.register(Error_Monitor)
class MonitorErrorAdmin(GeneratedErrorAdmin):
    list_display = ('inspection', 'error_message')
    raw_id_fields = ('inspection',)
