from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Dict lookup by a template variable key (Django's dot-lookup only
    supports literal attribute names, not a loop variable as the key)."""
    return mapping.get(key, []) if mapping else []


@register.filter
def join_titles(tasks):
    """Semicolon-joined task titles, for a day cell's hover tooltip."""
    return "; ".join(t.title for t in tasks)


def _file_ext(document):
    name = document.file.name
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


@register.filter
def ext_in(document, allowed_formats):
    """True if `document`'s file extension is in `allowed_formats` (a set
    computed once per request — see views.get_access_context). None means
    unrestricted (management/superuser)."""
    if allowed_formats is None:
        return True
    return _file_ext(document) in allowed_formats


@register.filter
def file_ext(document):
    """Uppercase file extension for display, e.g. 'PDF'."""
    return _file_ext(document).upper()
