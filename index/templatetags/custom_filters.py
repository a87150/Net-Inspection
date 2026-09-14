import json

from django import template

register = template.Library()

@register.filter
def attr(obj, attr_name):
    return getattr(obj, attr_name, None)


@register.filter
def pretty_json(value):
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)
