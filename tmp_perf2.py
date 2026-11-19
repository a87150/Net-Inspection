import os, tempfile
db = os.path.join(tempfile.mkdtemp(), "hc.sqlite3")
os.environ.update({"DB_ENGINE": "sqlite", "DJANGO_SQLITE_PATH": db, "DJANGO_DEBUG": "false"})
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "net.settings")
import django; django.setup()
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.test import Client
from django.contrib.auth import get_user_model
from django.core.management import call_command
call_command("migrate", verbosity=0)
call_command("seed_demo_data", verbosity=0)
c = Client(SERVER_NAME="127.0.0.1")
c.force_login(get_user_model().objects.filter(is_superuser=True).first())
with CaptureQueriesContext(connection) as ctx:
    r = c.get("/")
print("首页 status", r.status_code, "SQL", len(ctx.captured_queries))
from net.models import TaskRun
print("任务数", TaskRun.objects.count())