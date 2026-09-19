"""Regressions for the four Task 5 review findings."""

import hashlib
import json
import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connections, transaction
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from tests.devices.pc.helpers import create_log_file

from net.models import Computer, ComputerAnalysisProfile, ComputerLogFile, RecordStatus
from net.devices.pc.analysis import analyze_log
from net.devices.pc import logs as computer_logs


class SelectedItemSchemaTests(TestCase):
    NORMAL = {
        'activation': {'Windows激活信息': {'许可证状态': '已授权'}},
        'software': {'已安装软件列表': [{'软件名': 'Editor'}]},
        'processes': {'当前运行进程清单': [{'进程名': 'explorer'}]},
        'bitlocker': {'BitLocker状态': {'磁盘卷信息': [{'卷': 'C', '转换状态': '完全加密'}]}},
        'defender': {'WindowsDefender状态': {'当前病毒库版本': '1.2.3', '上次更新时间': '2026-08-31 09:00:00'}},
        'patches': {'系统更新历史': [{'补丁名称': 'KB123', '日期': '2026-08-31 09:00:00'}]},
        'domain_trust': {'当前与域服务器通讯情况': '正常通讯'},
        'group_policy': {'已应用策略': {'计算机策略': [], '用户策略': []}},
        'resource': {'计算机硬件资源情况': {'当前CPU占用率': '23%', '当前内存使用率': '48%'}},
        'event_findings': {'事件发现': [{'级别': 'Information', '消息': 'Started'}]},
    }

    def setUp(self):
        computer = Computer.objects.create(computer_name='SCHEMA-PC')
        self.log = create_log_file(
            computer=computer, source_path='fixture.json', modified_at=timezone.now(),
            content_hash='a' * 64, import_status='imported', payload={},
        )

    def analyze(self, item, fields):
        self.log.payload = {'系统信息概览': {'计算机名': 'SCHEMA-PC'}, **fields}
        return analyze_log(self.log, [item])

    def test_every_selected_item_absent_is_failed_and_explicitly_missing(self):
        for item in self.NORMAL:
            with self.subTest(item=item):
                result = self.analyze(item, {})
                self.assertEqual(result.result_level, 'info')
                self.assertEqual(result.exceptions[0]['analysis_item'], item)
                self.assertEqual(result.exceptions[0]['data_state'], 'missing')

    def test_every_selected_item_unknown_or_wrong_type_is_not_healthy(self):
        for item, fields in self.NORMAL.items():
            for unknown in (None, '未知', {'error': '未采集'}, [None]):
                with self.subTest(item=item, unknown=unknown):
                    result = self.analyze(item, {key: unknown for key in fields})
                    self.assertEqual(result.result_level, 'info')
                    self.assertEqual(result.exceptions[0]['data_state'], 'unknown')

    def test_every_selected_item_normal_schema_can_be_analyzed(self):
        for item, fields in self.NORMAL.items():
            with self.subTest(item=item):
                result = self.analyze(item, fields)
                self.assertEqual(result.status, RecordStatus.SUCCESS)
                self.assertEqual(result.exceptions, [])
                self.assertEqual(
                    set(result.details) - {
                        'collection_diagnostics', 'enrichment', 'rules', 'platform',
                        'severity_counts', 'health_status',
                    },
                    {item},
                )

    def test_present_empty_collections_are_distinct_from_missing(self):
        for item, key in (
            ('software', '已安装软件列表'), ('processes', '当前运行进程清单'),
            ('patches', '系统更新历史'), ('event_findings', '事件发现'),
        ):
            with self.subTest(item=item):
                result = self.analyze(item, {key: []})
                self.assertEqual(result.status, RecordStatus.SUCCESS)
                self.assertEqual(result.details[item], {'data_state': 'known', 'count': 0})
        result = self.analyze('bitlocker', {'BitLocker状态': {'磁盘卷信息': []}})
        self.assertEqual(result.result_level, 'info')
        self.assertEqual(result.exceptions[0]['data_state'], 'empty')

    def test_nested_unknown_status_and_resource_values_are_not_healthy(self):
        cases = {
            'activation': {'Windows激活信息': {'许可证状态': '未知'}},
            'defender': {'WindowsDefender状态': {'当前病毒库版本': '', '上次更新时间': ''}},
            'resource': {'计算机硬件资源情况': {'当前CPU占用率': '获取失败', '当前内存使用率': '未知'}},
            'domain_trust': {'当前与域服务器通讯情况': '未知'},
            'event_findings': {'事件发现': [{'级别': '未知'}]},
        }
        for item, fields in cases.items():
            with self.subTest(item=item):
                result = self.analyze(item, fields)
                self.assertEqual(result.result_level, 'info')
                self.assertEqual(result.exceptions[0]['data_state'], 'unknown')

    def test_empty_primary_events_do_not_fall_through_to_legacy_alias(self):
        result = self.analyze('event_findings', {
            '事件发现': [], '事件日志': [{'级别': 'error', '消息': 'old data'}],
        })
        self.assertEqual(result.status, RecordStatus.SUCCESS)
        self.assertEqual(result.details['event_findings'], {'data_state': 'known', 'count': 0})
