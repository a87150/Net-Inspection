"""Turn table rows into plain dicts, so a page and its REST twin agree.

The server-rendered tables loop over TableDefinition.fields and format each
value; this module does the same in Python, giving the client one shape.
"""
from index.templatetags.extras import format_table_field


def column_payloads(definition):
    return [
        {
            "key": field.key,
            "label": field.label,
            "kind": field.kind,
            "default_visible": bool(field.default_visible),
            "sortable": bool(field.sortable),
        }
        for field in definition.fields
    ]


def _raw_value(record, field):
    if isinstance(record, dict):
        return record.get(field.source)
    return getattr(record, field.source, None)


def row_payloads(records, definition, badges=None):
    """badges maps a field key to a callable returning {status, label} or None."""
    badges = badges or {}
    rows = []
    for record in records:
        cells = {}
        for field in definition.fields:
            badge = badges.get(field.key)
            if badge:
                resolved = badge(record)
                if resolved:
                    cells[field.key] = {"badge": resolved, "text": resolved["label"]}
                    continue
            cells[field.key] = {
                "text": format_table_field(_raw_value(record, field), field),
            }
        rows.append({
            "cells": cells,
            "url": (record.get("url") if isinstance(record, dict) else None) or "",
        })
    return rows


def table_payload(page_obj, definition, rows, extra=None):
    payload = {
        "columns": column_payloads(definition),
        "rows": rows,
        "count": page_obj.paginator.count,
        "page": page_obj.number,
        "pages": page_obj.paginator.num_pages,
        "page_size": page_obj.paginator.per_page,
        "start_index": page_obj.start_index() if page_obj.paginator.count else 0,
        "end_index": page_obj.end_index() if page_obj.paginator.count else 0,
    }
    if extra:
        payload.update(extra)
    return payload
