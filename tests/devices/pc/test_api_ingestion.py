import json
import base64
from django.test import TestCase, override_settings
from django.utils import timezone
from net.models import ComputerLogFile, PCUploadConfig, ComputerAnalysisProfile
from net.devices.pc.ingestion import ingest_log
from net.devices.pc.analysis_scope import latest_logs

@override_settings(PC_LOG_SOURCE_ENCRYPTION_KEY=base64.urlsafe_b64encode(b'0'*32).decode())
class APIIngestionTests(TestCase):
    def setUp(self):
        self.config = PCUploadConfig(endpoint_url='http://localhost/api/pc/logs/')
        self.config.set_token('test-token-with-at-least-thirty-two-characters')
        self.config.save()

    def raw(self, time='2026-09-16 10:00:00', name='PC-TEST'):
        return json.dumps({'platform':'windows','日志时间':time,'系统信息概览':{'计算机名':name},'已安装软件列表':[],'计算机硬件资源情况':{'CPU型号':'CPU TEST'}}, ensure_ascii=False).encode()

    def test_authenticated_upload_and_duplicate(self):
        self.assertEqual(self.client.post('/api/pc/logs/', self.raw(), content_type='application/json').status_code,401)
        response=self.client.post('/api/pc/logs/',self.raw(),content_type='application/json',HTTP_AUTHORIZATION='Bearer '+self.config.get_token())
        self.assertEqual(response.status_code,201,response.content)
        duplicate=self.client.post('/api/pc/logs/',self.raw(),content_type='application/json',HTTP_AUTHORIZATION='Bearer '+self.config.get_token())
        self.assertEqual(duplicate.status_code,200)
        log=ComputerLogFile.objects.get()
        self.assertEqual(log.system_info['计算机名'],'PC-TEST')
        self.assertEqual(log.payload['已安装软件列表'],[])
        self.assertNotIn('network_info',log.payload)

    def test_daily_latest_out_of_order_and_past_days(self):
        ingest_log(self.raw('2026-09-15 09:00:00'))
        ingest_log(self.raw('2026-09-16 11:00:00'))
        ingest_log(self.raw('2026-09-16 10:00:00'))
        ingest_log(self.raw('2026-09-16 12:00:00'))
        self.assertEqual(ComputerLogFile.objects.count(),2)
        self.assertEqual(timezone.localtime(latest_logs().get().collected_at).hour,12)

    def test_all_preserves_versions_but_latest_selection_is_single(self):
        self.config.log_retention='all'; self.config.save()
        ingest_log(self.raw()); ingest_log(self.raw('2026-09-16 12:00:00'))
        self.assertEqual(ComputerLogFile.objects.count(),2)
        self.assertEqual(latest_logs().count(),1)

    def test_invalid_payload_not_saved(self):
        for raw in (b'[]',b'{}',b'{"platform":"linux"}', b'{"value":NaN}'):
            response=self.client.post('/api/pc/logs/',raw,content_type='application/json',HTTP_AUTHORIZATION='Bearer '+self.config.get_token())
            self.assertEqual(response.status_code,400,response.content)
        self.assertEqual(ComputerLogFile.objects.count(),0)

    def test_queued_log_is_protected_until_task_finishes(self):
        from net.devices.pc.analysis_scope import enqueue_latest_analysis
        from net.devices.pc.retention import cleanup_retained_logs
        from net.inspections.queue import cancel_task
        first = ingest_log(self.raw()).log_file
        profile = ComputerAnalysisProfile.objects.create(name='Latest', analysis_items=['system'])
        task = enqueue_latest_analysis(profile, 'manual')
        newest = ingest_log(self.raw('2026-09-16 12:00:00')).log_file
        self.assertTrue(ComputerLogFile.objects.filter(pk=first.pk).exists())
        self.assertEqual(task.target_runs.get().target_id, str(first.pk))
        self.assertEqual(latest_logs().get().pk, newest.pk)
        cancel_task(task.pk)
        cleanup_retained_logs()
        self.assertFalse(ComputerLogFile.objects.filter(pk=first.pk).exists())

    def test_manual_old_selection_uses_latest_and_blocks_overlap(self):
        from django.core.exceptions import ValidationError
        from net.inspections.queue import enqueue_task
        self.config.log_retention='all'; self.config.save()
        first=ingest_log(self.raw()).log_file
        newest=ingest_log(self.raw('2026-09-16 11:00:00')).log_file
        profile=ComputerAnalysisProfile.objects.create(name='Scope', analysis_items=['system'])
        task=enqueue_task(profile,[first.pk],'manual')
        self.assertEqual(task.target_runs.get().target_id,str(newest.pk))
        ingest_log(self.raw('2026-09-16 12:00:00'))
        with self.assertRaises(ValidationError):
            enqueue_task(profile,[first.pk],'manual')

    def test_analysis_retention_preserves_other_days_profiles_and_all_mode(self):
        from datetime import timedelta
        from net.models import ComputerAnalysis, TaskRun, TaskTargetRun
        from net.devices.pc.analysis import prepare_log, persist_analysis
        log=ingest_log(self.raw()).log_file
        profile=ComputerAnalysisProfile.objects.create(name='Retention', analysis_items=['system'])
        def result(mode='daily_latest', selected_profile=profile):
            task=TaskRun.objects.create(task_type='computer_analysis', source='manual', status='success', finished_at=timezone.now(), progress=100, alert_summary_processed_at=timezone.now(),
                analysis_profile=selected_profile, profile_snapshot={'analysis_retention':mode})
            target=TaskTargetRun.objects.create(task=task,target_type='computer_log',target_id=str(log.pk),alert_processed_at=timezone.now())
            return persist_analysis(prepare_log(log,['system'],task_target=target))
        yesterday=result()
        ComputerAnalysis.objects.filter(pk=yesterday.pk).update(analysis_date=timezone.localdate()-timedelta(days=1))
        old=result(); latest=result()
        self.assertFalse(ComputerAnalysis.objects.filter(pk=old.pk).exists())
        self.assertTrue(ComputerAnalysis.objects.filter(pk=yesterday.pk).exists())
        other=ComputerAnalysisProfile.objects.create(name='Other', analysis_items=['system'])
        result(selected_profile=other)
        result('all')
        self.assertTrue(ComputerAnalysis.objects.filter(pk=latest.pk).exists())
        self.assertEqual(ComputerAnalysis.objects.count(),4)

    def test_analysis_references_id_without_foreign_key_or_source_lists(self):
        from net.models import ComputerAnalysis
        from net.devices.pc.analysis import analyze_log
        log=ingest_log(self.raw()).log_file
        analysis=analyze_log(log,['system','software','processes'])
        self.assertEqual(analysis.log_id,log.pk)
        self.assertFalse(ComputerAnalysis._meta.get_field('log_id').is_relation)
        self.assertEqual(analysis.details['software']['count'],0)
        self.assertNotIn('计算机名',json.dumps(analysis.details['system'],ensure_ascii=False))
        identity=log.pk
        log.delete()
        analysis.refresh_from_db()
        self.assertEqual(analysis.log_id,identity)
        self.assertIsNone(analysis.log_file)
