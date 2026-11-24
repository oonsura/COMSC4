from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    if not isinstance(dictionary, dict):
        return None
    if key in dictionary:
        return dictionary[key]
    return dictionary.get(str(key))


@register.filter
def zfill5(value):
    try:
        return str(value).strip().zfill(5)
    except Exception:
        return str(value)


@register.simple_tag
def seat_key(r, c):
    return f'{r}_{c}'


@register.filter
def zfill6(value):
    try:
        return str(value).strip().zfill(6)
    except Exception:
        return str(value)
