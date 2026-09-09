"""Fixture JSON that preserves database microseconds instead of milliseconds."""
from datetime import datetime, time
import json
from django.core.serializers.json import DjangoJSONEncoder, Serializer as JSONSerializer


class ExactJSONEncoder(DjangoJSONEncoder):
    def default(self, value):
        if isinstance(value, datetime):
            return value.isoformat().replace('+00:00', 'Z')
        if isinstance(value, time):
            if value.tzinfo is not None:
                raise ValueError('Timezone-aware time values cannot be migrated.')
            return value.isoformat()
        return super().default(value)


class Serializer(JSONSerializer):
    def handle_m2m_field(self, obj, field):
        super().handle_m2m_field(obj, field)
        if field.name in self._current:
            # M2M membership is a set; MariaDB UUID ordering differs from SQLite.
            self._current[field.name].sort(key=lambda value: json.dumps(value, cls=ExactJSONEncoder, sort_keys=True))

    def _init_options(self):
        super()._init_options()
        self.json_kwargs['cls'] = ExactJSONEncoder
