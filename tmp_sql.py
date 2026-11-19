import os, collections
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
    c.get("/")
for i, q in enumerate(ctx.captured_queries, 1):
    sql = " ".join(q["sql"].split())[:110]
    print(i, sql)