from importlib import import_module

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from net.models import InspectionProfile, TaskRun, TaskTargetRun


alert_models = import_module('net.models')


def _model(name):
    return getattr(alert_models, name, None)


def _form(name):
    try:
        forms_module = import_module('index.alerts.forms')
    except ModuleNotFoundError:
        return None
    return getattr(forms_module, name, None)


class AlertModelContractTests(TestCase):
    def setUp(self):
        self.profile = InspectionProfile.objects.create(
            name='告警服务器配置',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['connectivity'],
        )

    def _require_models(self):
        classes = {
            name: _model(name)
            for name in (
                'AlertChannel',
                'AlertPolicy',
                'AlertState',
                'AlertEvent',
                'AlertDelivery',
            )
        }
        self.assertTrue(all(classes.values()), '告警模型必须从 net.models 导出。')
        return classes

    def _channel(self, *, channel_type='feishu', name='飞书告警'):
        AlertChannel = self._require_models()['AlertChannel']
        if channel_type == 'email':
            settings = {
                'smtp_host': 'smtp.example.com',
                'smtp_port': 587,
                'use_tls': True,
                'use_ssl': False,
                'username': 'alerts@example.com',
                'password': 'mail-secret',
                'from_email': 'alerts@example.com',
                'recipients': ['ops@example.com'],
            }
        else:
            settings = {
                'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/test',
                'secret': 'robot-secret',
            }
            if channel_type == 'dingtalk':
                settings['webhook_url'] = (
                    'https://oapi.dingtalk.com/robot/send?access_token=test'
                )
        return AlertChannel.objects.create(
            name=name,
            channel_type=channel_type,
            settings=settings,
        )

    def _default_policy(self, channel=None):
        AlertPolicy = self._require_models()['AlertPolicy']
        policy = AlertPolicy.objects.create(
            name='全局默认告警',
            is_default=True,
            mode=AlertPolicy.Mode.OVERRIDE,
        )
        if channel is not None:
            policy.channels.add(channel)
        return policy

    def _target_run(self):
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION,
            inspection_profile=self.profile,
            scope_key='a' * 64,
            active_scope_key='a' * 64,
            total_targets=1,
        )
        return TaskTargetRun.objects.create(
            task=task,
            target_type=TaskTargetRun.TargetType.SERVER,
            target_id='server-1',
        )

    def _event(self, channel):
        AlertEvent = self._require_models()['AlertEvent']
        target_run = self._target_run()
        return AlertEvent.objects.create(
            task=target_run.task,
            target_run=target_run,
            profile_type='inspection_profile',
            profile_id=str(self.profile.pk),
            target_type=target_run.target_type,
            target_id=target_run.target_id,
            event_type=AlertEvent.EventType.ABNORMAL,
            findings=[{
                'key': 'connectivity.unreachable',
                'severity': 'critical',
                'title': '目标不可达',
                'detail': '无法建立连接',
            }],
        )

    def test_net_models_exports_all_alert_contract_models(self):
        self._require_models()

    def test_only_one_default_policy_can_be_persisted_portably(self):
        self._require_models()
        self._default_policy()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._default_policy()

    def test_policy_supports_multiple_channels_and_resolves_inheritance(self):
        classes = self._require_models()
        AlertPolicy = classes['AlertPolicy']
        feishu = self._channel()
        email = self._channel(channel_type='email', name='邮件告警')
        default_policy = self._default_policy(feishu)
        override = AlertPolicy.objects.create(
            name='服务器覆盖策略',
            inspection_profile=self.profile,
            mode=AlertPolicy.Mode.OVERRIDE,
        )
        override.channels.add(feishu, email)
        override.full_clean()
        self.assertEqual(
            set(override.effective_channels()),
            {feishu, email},
        )

        inherited = AlertPolicy.objects.create(
            name='服务器继承策略',
            inspection_profile=InspectionProfile.objects.create(
                name='继承告警服务器配置',
                device_type=InspectionProfile.DeviceType.SERVER,
                selected_items=['connectivity'],
            ),
            mode=AlertPolicy.Mode.INHERIT,
        )
        inherited.full_clean()
        self.assertEqual(set(inherited.effective_channels()), {feishu})
        self.assertEqual(default_policy.default_slot, AlertPolicy.DEFAULT_SLOT)

    def test_invalid_inheritance_and_override_are_rejected(self):
        classes = self._require_models()
        AlertPolicy = classes['AlertPolicy']
        feishu = self._channel()
        inherited = AlertPolicy.objects.create(
            name='错误继承策略',
            inspection_profile=self.profile,
            mode=AlertPolicy.Mode.INHERIT,
        )
        inherited.channels.add(feishu)
        with self.assertRaises(ValidationError) as inherited_error:
            inherited.full_clean()
        self.assertIn('channels', inherited_error.exception.message_dict)

        overriding = AlertPolicy.objects.create(
            name='错误覆盖策略',
            inspection_profile=InspectionProfile.objects.create(
                name='覆盖告警服务器配置',
                device_type=InspectionProfile.DeviceType.SERVER,
                selected_items=['connectivity'],
            ),
            mode=AlertPolicy.Mode.OVERRIDE,
        )
        with self.assertRaises(ValidationError) as override_error:
            overriding.full_clean()
        self.assertIn('channels', override_error.exception.message_dict)

        no_default = AlertPolicy.objects.create(
            name='无默认策略的继承',
            inspection_profile=InspectionProfile.objects.create(
                name='无默认配置',
                device_type=InspectionProfile.DeviceType.SERVER,
                selected_items=['connectivity'],
            ),
            mode=AlertPolicy.Mode.INHERIT,
        )
        with self.assertRaises(ValidationError) as default_error:
            no_default.full_clean()
        self.assertIn('mode', default_error.exception.message_dict)

    def test_state_is_unique_per_profile_target_and_finding(self):
        AlertState = self._require_models()['AlertState']
        kwargs = {
            'profile_type': 'inspection_profile',
            'profile_id': str(self.profile.pk),
            'target_type': 'server',
            'target_id': 'server-1',
            'finding_key': 'connectivity.unreachable',
        }
        AlertState.objects.create(**kwargs)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AlertState.objects.create(**kwargs)

    def test_event_and_delivery_are_deduplicated_and_retry_is_finite(self):
        classes = self._require_models()
        AlertEvent = classes['AlertEvent']
        AlertDelivery = classes['AlertDelivery']
        channel = self._channel()
        event = self._event(channel)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AlertEvent.objects.create(
                    task=event.task,
                    target_run=event.target_run,
                    profile_type=event.profile_type,
                    profile_id=event.profile_id,
                    target_type=event.target_type,
                    target_id=event.target_id,
                    event_type=AlertEvent.EventType.ABNORMAL,
                    findings=[],
                )

        AlertDelivery.objects.create(event=event, channel=channel)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AlertDelivery.objects.create(event=event, channel=channel)

        limited = AlertDelivery(
            event=event,
            channel=self._channel(name='飞书告警二号'),
            attempt_count=4,
            max_attempts=3,
        )
        with self.assertRaises(ValidationError) as retry_error:
            limited.full_clean()
        self.assertIn('attempt_count', retry_error.exception.message_dict)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AlertDelivery.objects.create(
                    event=event,
                    channel=self._channel(name='飞书告警三号'),
                    max_attempts=0,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AlertDelivery.objects.create(
                    event=event,
                    channel=self._channel(name='飞书告警四号'),
                    status='unbounded',
                )

    def test_channel_settings_reject_non_finite_values_and_invalid_transport(self):
        AlertChannel = self._require_models()['AlertChannel']
        invalid_json = AlertChannel(
            name='非有限值渠道',
            channel_type=AlertChannel.ChannelType.FEISHU,
            settings={
                'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/test',
                'timeout': float('nan'),
            },
        )
        with self.assertRaises(ValidationError) as json_error:
            invalid_json.full_clean()
        self.assertIn('settings', json_error.exception.message_dict)

        invalid_email = AlertChannel(
            name='错误邮件渠道',
            channel_type=AlertChannel.ChannelType.EMAIL,
            settings={
                'smtp_host': 'smtp.example.com',
                'smtp_port': 587,
                'use_tls': True,
                'use_ssl': True,
                'from_email': 'not-an-email',
                'recipients': ['also-not-an-email'],
            },
        )
        with self.assertRaises(ValidationError) as email_error:
            invalid_email.full_clean()
        self.assertIn('settings', email_error.exception.message_dict)

    def test_model_string_representations_never_include_secret_values(self):
        classes = self._require_models()
        channel = self._channel()
        event = self._event(channel)
        delivery = classes['AlertDelivery'](event=event, channel=channel)
        for value in (str(channel), repr(channel), str(event), repr(event), str(delivery), repr(delivery)):
            self.assertNotIn('robot-secret', value)
            self.assertNotIn('access_token=test', value)


class AlertChannelSecretFormTests(TestCase):
    SECRET = 'do-not-render-this-secret'

    def _channel(self, channel_type, *, settings):
        AlertChannel = _model('AlertChannel')
        self.assertIsNotNone(AlertChannel, 'AlertChannel 必须已导出。')
        return AlertChannel.objects.create(
            name=f'{channel_type}-渠道',
            channel_type=channel_type,
            settings=settings,
        )

    def test_feishu_form_masks_existing_secret_and_blank_submission_preserves_it(self):
        FeishuAlertChannelForm = _form('FeishuAlertChannelForm')
        self.assertIsNotNone(FeishuAlertChannelForm, '必须提供飞书告警渠道表单。')
        channel = self._channel('feishu', settings={
            'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/test',
            'secret': self.SECRET,
        })
        form = FeishuAlertChannelForm(instance=channel)
        self.assertEqual(form.initial['secret'], '••••••••')
        self.assertNotIn(self.SECRET, form.as_p())
        saved = FeishuAlertChannelForm(data={
            'name': channel.name,
            'is_enabled': 'on',
            'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/changed',
            'secret': '',
        }, instance=channel)
        self.assertTrue(saved.is_valid(), saved.errors)
        persisted = saved.save()
        self.assertEqual(persisted.settings['secret'], self.SECRET)
        self.assertEqual(
            persisted.settings['webhook_url'],
            'https://open.feishu.cn/open-apis/bot/v2/hook/changed',
        )

    def test_blank_secret_can_create_an_unsigned_feishu_channel(self):
        FeishuAlertChannelForm = _form('FeishuAlertChannelForm')
        self.assertIsNotNone(FeishuAlertChannelForm, '必须提供飞书告警渠道表单。')
        form = FeishuAlertChannelForm(data={
            'name': '无签名飞书渠道',
            'is_enabled': 'on',
            'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/unsigned',
            'secret': '',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().settings['secret'], '')

    def test_dingtalk_form_replaces_secret_without_echoing_it_in_errors(self):
        DingTalkAlertChannelForm = _form('DingTalkAlertChannelForm')
        self.assertIsNotNone(DingTalkAlertChannelForm, '必须提供钉钉告警渠道表单。')
        channel = self._channel('dingtalk', settings={
            'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=test',
            'secret': self.SECRET,
        })
        form = DingTalkAlertChannelForm(data={
            'name': channel.name,
            'is_enabled': 'on',
            'webhook_url': 'http://not-secure.example.test/hook',
            'secret': 'replacement-secret',
        }, instance=channel)
        self.assertFalse(form.is_valid())
        self.assertNotIn('replacement-secret', str(form.errors))
        self.assertNotIn(self.SECRET, str(form.errors))

    def test_email_form_validates_tls_email_and_preserves_blank_password(self):
        EmailAlertChannelForm = _form('EmailAlertChannelForm')
        self.assertIsNotNone(EmailAlertChannelForm, '必须提供邮件告警渠道表单。')
        channel = self._channel('email', settings={
            'smtp_host': 'smtp.example.com',
            'smtp_port': 465,
            'use_tls': False,
            'use_ssl': True,
            'username': 'alerts@example.com',
            'password': self.SECRET,
            'from_email': 'alerts@example.com',
            'recipients': ['ops@example.com'],
        })
        form = EmailAlertChannelForm(instance=channel)
        self.assertEqual(form.initial['password'], '••••••••')
        self.assertNotIn(self.SECRET, form.as_p())

        invalid = EmailAlertChannelForm(data={
            'name': channel.name,
            'is_enabled': 'on',
            'smtp_host': 'smtp.example.com',
            'smtp_port': '587',
            'use_tls': 'on',
            'use_ssl': 'on',
            'username': 'alerts@example.com',
            'password': 'another-secret',
            'from_email': 'not-an-email',
            'recipients': 'ops@example.com, not-an-email',
        }, instance=channel)
        self.assertFalse(invalid.is_valid())
        self.assertNotIn('another-secret', str(invalid.errors))

        valid = EmailAlertChannelForm(data={
            'name': channel.name,
            'is_enabled': 'on',
            'smtp_host': 'smtp.example.com',
            'smtp_port': '587',
            'use_tls': 'on',
            'username': 'alerts@example.com',
            'password': '',
            'from_email': 'alerts@example.com',
            'recipients': 'ops@example.com, security@example.com',
        }, instance=channel)
        self.assertTrue(valid.is_valid(), valid.errors)
        persisted = valid.save()
        self.assertEqual(persisted.settings['password'], self.SECRET)
        self.assertEqual(
            persisted.settings['recipients'],
            ['ops@example.com', 'security@example.com'],
        )
