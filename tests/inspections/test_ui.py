"""HTTP contracts for configuring and following Phase 2 background tasks."""

from datetime import time
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from net.models import (
    ComputerAnalysisProfile,
    ComputerLogFile,
    InspectionProfile,
    Schedule,
    Server,
    TaskRun,
)
from net.inspections.queue import enqueue_task


class TaskUiTestCase(TestCase):
    def setUp(self):
        self.linux = Server.objects.create(
            name='Linux 应用服务器', ip='192.0.2.210', server_type='linux',
        )
        self.windows = Server.objects.create(
            name='Windows 文件服务器', ip='192.0.2.211', server_type='windows',
        )
        self.server_profile = InspectionProfile.objects.create(
            name='服务器默认巡检',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu', 'memory'],
            timeout_seconds=90,
            concurrent_workers=3,
        )
        self.log_profile = ComputerAnalysisProfile.objects.create(
            name='计算机默认分析',
            scan_directories=['C:/inspection-logs'],
            processed_directory='C:/inspection-logs/processed',
            failed_directory='C:/inspection-logs/failed',
            analysis_items=['activation', 'resource'],
            concurrent_workers=2,
        )

    def post_manual(self, data, *, follow=False):
        return self.client.post('/tasks/manual/', data, follow=follow)

    def test_infrastructure_pages_place_configuration_beside_manual_inspection(self):
        """Removing modal entrypoints would leave manual inspection unconfigurable."""
        for kind in ('networks', 'servers', 'monitors'):
            with self.subTest(kind=kind):
                response = self.client.get(reverse('asset_list', args=[kind]))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '手动执行巡检')
                self.assertContains(response, '巡检配置')
                self.assertContains(response, 'data-bs-target="#runTaskModal"')
                self.assertContains(response, 'data-bs-target="#profileConfigModal"')

    def test_computer_pages_offer_manual_analysis_and_scan_range_controls(self):
        """A computer page without this form would force scan/analysis back into Web requests."""
        for url in (
            reverse('asset_list', args=['computers']),
            reverse('computer_analysis_list'),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '手动执行分析')
                self.assertContains(response, '分析配置')
                self.assertContains(response, '扫描目录')
                self.assertContains(response, '最近 N 天')
                self.assertContains(response, '指定起止日期')
                self.assertContains(response, '最低 Windows 版本')
                self.assertContains(response, 'Defender 病毒库最大间隔')
                self.assertContains(response, 'CPU 报警阈值')
                self.assertContains(response, 'KMS 服务器')
                html = response.content.decode()
                self.assertEqual(html.count('>保存配置</button>'), 1)
                self.assertNotIn('profile-config-save-top', html)
                self.assertContains(response, 'id="profileConfigModal"')
        css = Path(settings.BASE_DIR, 'static/app/css/style.css').read_text(encoding='utf-8')
        self.assertIn('#profileConfigModal .modal-body', css)
        self.assertIn('overflow-y: auto', css)

    def test_manual_selected_enqueue_snapshots_filtered_targets_items_and_concurrency(self):
        """Dropping UI scope validation could inspect an unfiltered asset or live profile setting."""
        response = self.post_manual({
            'profile_id': str(self.server_profile.pk),
            'target_mode': 'selected',
            'target_ids': [str(self.linux.pk)],
            'selected_items': ['cpu'],
            'concurrent_workers': '2',
            'next': reverse('asset_list', args=['servers']),
            'filter_server_type': 'linux',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            urlsplit(response['Location']).path,
            reverse('task_detail', args=[TaskRun.objects.get().pk]),
        )
        task = TaskRun.objects.get()
        self.assertEqual(task.task_type, TaskRun.TaskType.INSPECTION)
        self.assertEqual(task.source, TaskRun.Source.MANUAL)
        self.assertEqual(task.selected_items_snapshot, ['cpu'])
        self.assertEqual(task.parameters_snapshot['concurrent_workers'], 2)
        self.assertEqual(
            list(task.target_runs.values_list('target_id', flat=True)),
            [str(self.linux.pk)],
        )
        self.assertEqual(
            task.target_scope_snapshot['targets'],
            [{'target_type': 'server', 'target_id': str(self.linux.pk)}],
        )

    def test_manual_configured_run_uses_the_selected_profile_without_overrides(self):
        response = self.post_manual({
            'profile_id': str(self.server_profile.pk),
            'next': reverse('asset_list', args=['servers']),
        })

        self.assertEqual(response.status_code, 302)
        task = TaskRun.objects.get()
        self.assertEqual(task.selected_items_snapshot, ['cpu', 'memory'])
        self.assertEqual(
            set(task.target_runs.values_list('target_id', flat=True)),
            {str(self.linux.pk), str(self.windows.pk)},
        )

    def test_manual_run_modal_only_exposes_a_configured_profile_selector(self):
        response = self.client.get(reverse('asset_list', args=['servers']))

        self.assertContains(response, 'manual-config-runner')
        self.assertNotContains(response, '目标范围</legend>')
        self.assertNotContains(response, '执行项目</legend>')
        self.assertNotContains(response, 'manual-concurrency')

    def test_manual_all_and_filtered_enqueue_snapshot_the_requested_server_scope(self):
        """The all and filtered modes must remain distinct after the modal closes."""
        all_response = self.post_manual({
            'profile_id': str(self.server_profile.pk),
            'target_mode': 'all',
            'selected_items': ['cpu'],
            'next': reverse('asset_list', args=['servers']),
        })
        filtered_response = self.post_manual({
            'profile_id': str(self.server_profile.pk),
            'target_mode': 'filtered',
            'selected_items': ['memory'],
            'next': reverse('asset_list', args=['servers']),
            'filter_server_type': 'linux',
        })

        self.assertEqual(all_response.status_code, 302)
        self.assertEqual(filtered_response.status_code, 302)
        tasks = list(TaskRun.objects.order_by('created_at', 'pk'))
        self.assertEqual(len(tasks), 2)
        self.assertEqual(
            set(tasks[0].target_runs.values_list('target_id', flat=True)),
            {str(self.linux.pk), str(self.windows.pk)},
        )
        self.assertEqual(
            list(tasks[1].target_runs.values_list('target_id', flat=True)),
            [str(self.linux.pk)],
        )

    def test_record_pages_do_not_offer_unselectable_target_modes(self):
        """A record table has no target checkboxes, so a selected mode would be misleading."""
        response = self.client.get(reverse('record_list', args=['servers']))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '表格已选项目')

    def test_computer_pages_offer_only_configured_analysis_execution(self):
        """Computer and analysis rows are not direct targets; the configured scan owns them."""
        response = self.client.get(reverse('asset_list', args=['computers']))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '选择已配置任务')
        self.assertNotContains(response, '全部资产')
        self.assertNotContains(response, '表格已选项目')

    def test_manual_enqueue_rejects_selected_target_outside_active_filter_without_writes(self):
        """Trusting posted IDs could allow a user to bypass the current table scope."""
        response = self.post_manual({
            'profile_id': str(self.server_profile.pk),
            'target_mode': 'selected',
            'target_ids': [str(self.windows.pk)],
            'selected_items': ['cpu'],
            'next': reverse('asset_list', args=['servers']),
            'filter_server_type': 'linux',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(TaskRun.objects.count(), 0)
        self.assertContains(response, '当前筛选范围')
        self.assertContains(response, 'data-auto-open="true"')

    def test_duplicate_active_manual_task_reopens_modal_with_clear_message(self):
        """Hiding duplicate rejection would make a clicked action look silently lost."""
        enqueue_task(
            self.server_profile,
            [self.linux.pk],
            TaskRun.Source.MANUAL,
            overrides={'selected_items': ['cpu']},
        )

        response = self.post_manual({
            'profile_id': str(self.server_profile.pk),
            'target_mode': 'selected',
            'target_ids': [str(self.linux.pk)],
            'selected_items': ['cpu'],
            'next': reverse('asset_list', args=['servers']),
        }, follow=True)

        self.assertEqual(TaskRun.objects.count(), 1)
        self.assertContains(response, '已有活动任务')
        self.assertContains(response, 'data-auto-open="true"')

    def test_invalid_profile_input_reopens_configuration_without_changing_profile(self):
        """Saving arbitrary item keys would make later Worker selection unsafe."""
        response = self.client.post('/tasks/profiles/inspection/', {
            'profile_id': str(self.server_profile.pk),
            'name': self.server_profile.name,
            'device_type': 'server',
            'selected_items': ['not-registered'],
            'timeout_seconds': '60',
            'concurrent_workers': '4',
            'next': reverse('asset_list', args=['servers']),
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.server_profile.refresh_from_db()
        self.assertEqual(self.server_profile.selected_items, ['cpu', 'memory'])
        self.assertContains(response, '不支持的巡检项目')
        self.assertContains(response, 'data-auto-open="true"')

    def test_profile_schedule_form_updates_only_requested_profile(self):
        """Binding a schedule to a different profile would alter an unrelated project's plan."""
        other = InspectionProfile.objects.create(
            name='其他服务器配置',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
        )
        response = self.client.post('/tasks/profiles/inspection/', {
            'profile_id': str(self.server_profile.pk),
            'name': self.server_profile.name,
            'device_type': 'server',
            'selected_items': ['cpu', 'memory'],
            'timeout_seconds': '120',
            'concurrent_workers': '5',
            'schedule_enabled': 'on',
            'schedule_kind': 'daily',
            'daily_time': '03:15',
            'next': reverse('asset_list', args=['servers']),
        })

        self.assertEqual(response.status_code, 302)
        schedule = Schedule.objects.get(inspection_profile=self.server_profile)
        self.assertEqual(schedule.kind, Schedule.Kind.DAILY)
        self.assertEqual(schedule.daily_time, time(3, 15))
        self.assertTrue(schedule.is_enabled)
        self.assertFalse(Schedule.objects.filter(inspection_profile=other).exists())
        self.server_profile.refresh_from_db()
        self.assertEqual(self.server_profile.timeout_seconds, 120)
        self.assertEqual(self.server_profile.concurrent_workers, 5)

    def test_computer_scan_action_enqueues_worker_owned_scan_without_reading_files_inline(self):
        """Calling the scanner in this view would block the request and violate the Worker boundary."""
        response = self.post_manual({
            'profile_id': str(self.log_profile.pk),
            'target_mode': 'scan',
            'selected_items': ['activation'],
            'next': reverse('computer_analysis_list'),
        })

        self.assertEqual(response.status_code, 302)
        task = TaskRun.objects.get()
        self.assertEqual(task.task_type, 'computer_scan')
        self.assertEqual(task.target_runs.get().target_type, 'computer_scan')
        self.assertEqual(task.selected_items_snapshot, ['activation'])

    def test_task_list_and_detail_show_progress_errors_and_filtered_export(self):
        """Omitting target details would hide why a partial/failed task needs attention."""
        task = enqueue_task(
            self.server_profile,
            [self.linux.pk],
            TaskRun.Source.MANUAL,
        )
        target = task.target_runs.get()
        target.status = TaskRun.Status.FAILED
        target.started_at = timezone.now()
        target.finished_at = timezone.now()
        target.error_message = 'SSH 连接超时'
        target.save()

        listing = self.client.get('/tasks/', {'filter_status': 'queued'})
        detail = self.client.get(f'/tasks/{task.pk}/')

        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, '任务列表')
        self.assertContains(listing, '导出筛选结果')
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '目标执行详情')
        self.assertContains(detail, 'SSH 连接超时')
        self.assertContains(detail, '1 / 1')

    def test_task_progress_is_display_only_not_a_broken_queryset_filter(self):
        """Progress is a model property, so exposing ORM filter/sort controls would be fake."""
        response = self.client.get(reverse('task_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="filter_progress"')
        self.assertNotContains(response, 'data-sort-key="progress"')

    def test_active_task_pages_offer_authenticated_manual_stop(self):
        """Omitting the stop control would leave an operator unable to cancel stuck work."""
        task = enqueue_task(
            self.server_profile,
            [self.linux.pk],
            TaskRun.Source.MANUAL,
        )
        user = get_user_model().objects.create_user('operator', password='secret')
        self.client.force_login(user)

        listing = self.client.get(reverse('task_list'))
        detail = self.client.get(reverse('task_detail', args=[task.pk]))

        stop_url = reverse('task_cancel', args=[task.pk])
        for response in (listing, detail):
            self.assertContains(response, '结束任务')
            self.assertContains(response, f'action="{stop_url}"')

    def test_manual_stop_requires_login_and_cancels_the_task(self):
        """An unauthenticated or GET request must not mutate task execution state."""
        task = enqueue_task(
            self.server_profile,
            [self.linux.pk],
            TaskRun.Source.MANUAL,
        )
        stop_url = reverse('task_cancel', args=[task.pk])

        anonymous = self.client.post(stop_url)
        task.refresh_from_db()
        self.assertEqual(anonymous.status_code, 302)
        self.assertEqual(task.status, TaskRun.Status.QUEUED)

        user = get_user_model().objects.create_user('operator', password='secret')
        self.client.force_login(user)
        self.assertEqual(self.client.get(stop_url).status_code, 405)
        response = self.client.post(stop_url)

        self.assertRedirects(response, reverse('task_detail', args=[task.pk]))
        task.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.CANCELLED)
