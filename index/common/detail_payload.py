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


def detail_payload(*, title, eyebrow="", description="", actions=(), sections=()):
    return {
        "title": title,
        "eyebrow": eyebrow,
        "description": description,
        "actions": [action for action in actions if action],
        "sections": [section for section in sections if section],
    }


def link_action(label, url, variant=""):
    return {"kind": "link", "label": label, "url": url, "variant": variant or ""}
