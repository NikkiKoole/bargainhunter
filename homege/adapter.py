"""Register the home.ge source so searches.json can point at it."""
from core.adapter import register

from .http import BASE
from .parse import parse_detail, parse_list


@register
class HomegeAdapter:
    source = "homege"
    base = BASE
    parse_list = staticmethod(parse_list)
    parse_detail = staticmethod(parse_detail)
