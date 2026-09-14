from datetime import datetime, timezone, time
import json
from django.test import SimpleTestCase


class MigrationEncodingTests(SimpleTestCase):
    def test_migration_keeps_all_six_fractional_digits(self):
        from deploy.migration_json import ExactJSONEncoder
        value = datetime(2026, 9, 8, 10, 0, 0, 123456, tzinfo=timezone.utc)
        encoded = json.loads(json.dumps({'value': value}, cls=ExactJSONEncoder))
        self.assertEqual(encoded['value'], '2026-09-08T10:00:00.123456Z')
        encoded = json.loads(json.dumps({'value': time(10, 0, 0, 654321)}, cls=ExactJSONEncoder))
        self.assertEqual(encoded['value'], '10:00:00.654321')
