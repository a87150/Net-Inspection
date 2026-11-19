import os, django, json, re
os.environ['DB_NAME'] = 'net-test'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'net.settings')
django.setup()
from django.test import Client
from django.contrib.auth import get_user_model
c = Client(SERVER_NAME='127.0.0.1')
c.force_login(get_user_model().objects.filter(is_superuser=True).first())
r = c.get('/assets/networks/')
h = r.content.decode()
print('selection-column in html:', 'data-device-selection-column' in h)
print('select-all in html:', 'data-device-select-all' in h)
m = re.search(r'id="asset-workspace-bootstrap"[^>]*>(.*?)</script>', h, re.S)
b = json.loads(m.group(1))
print('selectAttr:', repr(b.get('selectAttr')), 'formId:', repr(b.get('formId')))
print('ctx task_allow:', r.context.get('task_allow_target_selection'))