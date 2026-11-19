import os
os.environ["DB_NAME"] = "net-test"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "net.settings")
import django; django.setup()
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.test import Client
from django.contrib.auth import get_user_model
c = Client(SERVER_NAME="127.0.0.1")
c.force_login(get_user_model().objects.filter(is_superuser=True).first())
with CaptureQueriesContext(connection) as ctx:
    r = c.get("/")
print("首页 status", r.status_code, "| SQL", len(ctx.captured_queries))
from net.models import TaskRun
print("任务总数", TaskRun.objects.count())
print("people 模式任务", TaskRun.objects.filter(task_type="computer_analysis", profile_snapshot__matching_mode="people").count())