from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Dict lookup by a template variable key (Django's dot-lookup only
    supports literal attribute names, not a loop variable as the key)."""
    return mapping.get(key, []) if mapping else []
