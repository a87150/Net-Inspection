import os, django, json, re
os.environ['DB_NAME'] = 'net-test'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'net.settings')
django.setup()
from django.test import Client
from django.contrib.auth import get_user_model
c = Client(SERVER_NAME='127.0.0.1')
c.force_login(get_user_model().objects.filter(is_superuser=True).first())
r = c.get('/assets/people/')
print('list status', r.status_code)
h = r.content.decode()
print('mount', 'asset-workspace' in h)
m = re.search(r'id="asset-workspace-bootstrap"[^>]*>(.*?)</script>', h, re.S)
b = json.loads(m.group(1)) if m else {}
print('keys', sorted(b.keys()))
print('cols', len(b.get('columns') or []), 'rows', len(b.get('rows') or []))
print('endpoint', b.get('endpoint'), '| detailBase', b.get('detailBase'))
pid = (b.get('rows') or [{}])[0].get('id')
print('row0 id', pid)
d = c.get('/assets/people/%s/detail.json' % pid)
print('detail status', d.status_code)
dj = d.json()
print('detail keys', sorted(dj.keys()))
print('sections', len(dj.get('sections') or []))