import os
os.environ.update({"DB_ENGINE": "sqlite", "DJANGO_SQLITE_PATH": os.path.join(os.environ.get("TEMP", "."), "hc-perf.sqlite3"), "DJANGO_DEBUG": "false"})
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "net.settings")
import django; django.setup()
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.test import Client
from django.contrib.auth import get_user_model
from net.management.commands.seed_demo_data import Command
from django.core.management import call_command
call_command("migrate", verbosity=0)
try:
    call_command("seed_demo_data", verbosity=0)
except Exception as exc:
    print("seed skip", type(exc).__name__)
c = Client(SERVER_NAME="127.0.0.1")
u = get_user_model().objects.filter(is_superuser=True).first()
c.force_login(u)
with CaptureQueriesContext(connection) as ctx:
    r = c.get("/")
print("首页 status", r.status_code, "SQL", len(ctx.captured_queries))