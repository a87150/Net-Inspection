"""写操作的 JSON 回执：模态框拿 JSON，普通表单拿重定向。

两种路径必须一直并存——禁用 JS 的浏览器走表单，模态框走 fetch。
"""

from django.test import TestCase
from django.urls import reverse

from index.common.form_json import wants_json
from net.models import AlertPolicy
from tests.auth import login_admin


class FormJsonContractTests(TestCase):
    def setUp(self):
        login_admin(self.client)
        self.url = reverse('alert_policy_save')
        self.payload = {'scope': 'default', 'mode': 'override', 'name': '默认告警策略'}

    def test_header_decides_the_response_shape(self):
        request = self.client.get(reverse('alert_list')).wsgi_request
        self.assertFalse(wants_json(request))

    def test_invalid_submission_answers_json_only_when_asked(self):
        payload = {**self.payload, 'mode': 'not-a-mode'}

        ajax = self.client.post(self.url, payload, headers={'X-Form-Ajax': '1'})
        self.assertEqual(ajax.status_code, 400)
        self.assertFalse(ajax.json()['ok'])
        self.assertTrue(ajax.json()['errors'])

        plain = self.client.post(self.url, payload)
        self.assertEqual(plain.status_code, 302)

    def test_valid_submission_returns_message_and_redirect(self):
        ajax = self.client.post(self.url, self.payload, headers={'X-Form-Ajax': '1'})

        self.assertEqual(ajax.status_code, 200)
        body = ajax.json()
        self.assertTrue(body['ok'])
        self.assertIn('默认告警策略', body['message'])
        self.assertTrue(body['redirect'])
        self.assertEqual(AlertPolicy.objects.get(default_slot=AlertPolicy.DEFAULT_SLOT).name,
                         '默认告警策略')
