from index.devices.views import (
    ASSET_PAGES,
    asset_detail,
    asset_list,
    item_list,
    person_detail,
    people_statistics,
    run_infrastructure_inspection,
)
from index.dashboard.views import index, with_latest_status
from index.domain.views import (
    domain_account_detail,
    domain_computer_detail,
    domain_group_detail,
    domain_controller_settings,
    domain_object_detail,
    domain_object_list,
)
from index.domain.operations import domain_account_import, domain_account_import_template, domain_operation_create, domain_operation_retry
from index.common.imports import (
    download_inventory_template,
    import_inventory,
)
from index.common.exports import table_export
from index.devices.pc.logs import computer_log_list, computer_log_detail, computer_log_analyze
from index.devices.configuration import configuration_download, configuration_zip
from index.devices.pc.scripts import pc_script_download
from index.people.integrations import people_provider_save, people_schedule_save, people_test, people_preview, people_apply, people_operation, people_task_status, people_task_acknowledge
from index.alerts.views import (
    alert_channel_save,
    alert_detail,
    alert_list,
    alert_modal_context,
    alert_policy_save,
    alert_test_send,
)
from index.inspections.tasks import (
    computer_analysis_profile_configure,
    inspection_profile_configure,
    manual_task_create,
    task_cancel,
    task_detail,
    task_list,
    task_modal_context,
)
from index.inspections.records import (
    RECORD_PAGES,
    _error_records,
    _inspection_records,
    computer_analysis_detail,
    computer_analysis_list,
    computer_error_list,
    error_records,
    infrastructure_inspection_detail,
    inspection_records,
    record_detail,
    record_list,
)


__all__ = [
    'ASSET_PAGES',
    'RECORD_PAGES',
    '_error_records',
    '_inspection_records',
    'asset_detail',
    'asset_list',
    'computer_analysis_detail',
    'computer_analysis_list',
    'computer_error_list',
    'domain_account_detail',
    'domain_computer_detail',
    'domain_group_detail',
    'domain_controller_settings',
    'domain_object_detail',
    'domain_object_list',
    'domain_operation_create',
    'domain_operation_retry',
    'domain_account_import',
    'domain_account_import_template',
    'download_inventory_template',
    'error_records',
    'import_inventory',
    'index',
    'infrastructure_inspection_detail',
    'inspection_records',
    'item_list',
    'person_detail',
    'people_statistics',
    'record_detail',
    'record_list',
    'run_infrastructure_inspection',
    'table_export',
    'configuration_download',
    'configuration_zip',
    'pc_script_download',
    'people_provider_save',
    'people_schedule_save',
    'people_test',
    'people_preview',
    'people_apply',
    'people_operation',
    'people_task_status',
    'people_task_acknowledge',
    'alert_channel_save',
    'alert_detail',
    'alert_list',
    'alert_modal_context',
    'alert_policy_save',
    'alert_test_send',
    'task_detail',
    'task_list',
    'task_modal_context',
    'manual_task_create',
    'task_cancel',
    'inspection_profile_configure',
    'computer_analysis_profile_configure',
    'with_latest_status',
]
