"""Read the JSON a Vue list page hydrates its table with."""
import json
import re


def bootstrap(response, script_id):
    match = re.search(
        rf'id="{script_id}"[^>]*>(.*?)</script>', response.content.decode(), re.S,
    )
    return json.loads(match.group(1)) if match else {}


def rows(response, script_id):
    return bootstrap(response, script_id).get("rows") or []
