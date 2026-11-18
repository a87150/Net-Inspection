"""Shape a detail page once, so the template and its REST twin agree.

A payload is {title, eyebrow, description, actions, sections}; a section is one
of three kinds, matching what the detail templates actually render.
"""


def field_section(heading, fields):
    """fields: [(label, value)] or [(label, value, badge)]."""
    items = []
    for entry in fields:
        label, value = entry[0], entry[1]
        item = {"label": label, "value": "" if value is None else str(value)}
        if len(entry) > 2 and entry[2]:
            item["badge"] = entry[2]
        items.append(item)
    return {"heading": heading, "kind": "fields", "fields": items}


def list_section(heading, items, empty=""):
    """items: [{label, meta?, detail?, badge?}]."""
    return {"heading": heading, "kind": "list", "items": list(items), "empty": empty}


def text_section(heading, text):
    return {"heading": heading, "kind": "text", "text": text or ""}


def alert_section(level, text):
    """level: success | warning | danger | secondary | info."""
    return {"heading": "", "kind": "alert", "level": level, "text": text or ""}


def table_section(heading, columns, rows, empty="", note="", pagination=None,
                  workspace=""):
    """columns: [label | {label, sort_url?, aria_sort?}]; a cell is a string or
    {text?, badge?, url?, label?, note?}. Cells stay strings when that is all the
    template showed."""
    normalised = [
        {"label": column} if isinstance(column, str) else dict(column)
        for column in columns
    ]
    return {
        "heading": heading,
        "kind": "table",
        "columns": normalised,
        "rows": [list(row) for row in rows],
        "empty": empty,
        "note": note,
        "pagination": pagination,
        "workspace": workspace,
    }


def form_section(heading, fields, submit, note="", inputs=(), confirm=""):
    """A POST block: read-only fields, optional inputs, one submit button."""
    return {
        "heading": heading,
        "kind": "form",
        "fields": [
            {"label": label, "value": "" if value is None else str(value)}
            for label, value in fields
        ],
        "inputs": list(inputs),
        "submit": {
            "label": submit[0],
            "url": submit[1],
            "variant": submit[2] if len(submit) > 2 else "danger",
            "confirm": confirm,
        },
        "note": note,
    }


def group_section(heading, summary, groups):
    """groups: [{title, count, items: [{label, detail, badge}]}]."""
    return {
        "heading": heading,
        "kind": "groups",
        "summary": summary or "",
        "groups": list(groups),
    }


def detail_payload(*, title, eyebrow="", description="", actions=(), sections=(),
                   disclosure=None, csrf=""):
    return {
        "title": title,
        "eyebrow": eyebrow,
        "description": description,
        "actions": [action for action in actions if action],
        "sections": [section for section in sections if section],
        "disclosure": disclosure,
        "csrf": csrf,
    }


def link_action(label, url, variant=""):
    return {"kind": "link", "label": label, "url": url, "variant": variant or ""}


def form_action(label, url, variant="danger", confirm=""):
    return {"kind": "form", "label": label, "url": url, "variant": variant,
            "confirm": confirm}
