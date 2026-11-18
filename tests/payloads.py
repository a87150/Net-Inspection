"""Read the JSON a Vue page hydrates its island with."""
import json
import re


def bootstrap(response, script_id):
    match = re.search(
        rf'id="{script_id}"[^>]*>(.*?)</script>', response.content.decode(), re.S,
    )
    return json.loads(match.group(1)) if match else {}


def rows(response, script_id):
    return bootstrap(response, script_id).get("rows") or []


def payload_text(response, script_id):
    """The hydrated JSON as one searchable string, for detail-page assertions."""
    return json.dumps(bootstrap(response, script_id), ensure_ascii=False)
