from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Look up a dict value by a variable key: {{ my_dict|get_item:some_var }}"""
    return dictionary.get(key)


@register.filter
def abs_value(value):
    """Return the absolute value of a number."""
    try:
        return abs(value)
    except (TypeError, ValueError):
        return value
