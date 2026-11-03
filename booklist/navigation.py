"""Bounded same-site GET destinations; never accept credentials or action routes."""
import re
from urllib.parse import urlsplit, unquote, urlencode

FRONT_PAGE=re.compile(r'/(?:works/[1-9][0-9]*/(?:edit/)?|activities/(?:[1-9][0-9]*/(?:recommend/)?|history/)?|recommend/|catalog/|feedback/|notifications/|account/(?:settings/|bind/)?|privacy/|guidelines/|contact/)?'.replace(' ',''))


def safe_return(value, fallback='/'):
    if not isinstance(value,str) or not value.startswith('/') or value.startswith('//') or len(value)>8192:
        return fallback
    decoded=unquote(value)
    if any(ord(c)<32 or ord(c)==127 for c in decoded) or '\\' in decoded:
        return fallback
    try:
        parts=urlsplit(value)
        if parts.scheme or parts.netloc or not FRONT_PAGE.fullmatch(parts.path):
            return fallback
    except ValueError:
        return fallback
    return value


def login_url(destination):
    return '/account/login/?'+urlencode({'next':safe_return(destination)})
