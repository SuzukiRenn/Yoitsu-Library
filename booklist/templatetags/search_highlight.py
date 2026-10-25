"""Highlight visible matches without changing retrieval or revealing aliases."""
from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()
ASCII_LOWER = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')


def match_parts(value, query, unicode_casefold=False):
    value, query = str(value or ''), str(query or '')
    if not query:
        return [(value, False)]
    fold = str.casefold if unicode_casefold else lambda text:text.translate(ASCII_LOWER)
    folded, positions = '', []
    for index, char in enumerate(value):
        piece = fold(char)
        folded += piece
        positions.extend([index] * len(piece))
    needle = fold(query)
    if not needle:
        return [(value, False)]
    ranges, offset = [], 0
    while (found := folded.find(needle,offset)) >= 0:
        start, end = positions[found], positions[found+len(needle)-1]+1
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0],max(end,ranges[-1][1]))
        else:
            ranges.append((start,end))
        offset = found+len(needle)
    parts, offset = [], 0
    for start,end in ranges:
        if start > offset:
            parts.append((value[offset:start],False))
        parts.append((value[start:end],True));offset=end
    if offset < len(value):
        parts.append((value[offset:],False))
    return parts or [(value,False)]


@register.simple_tag(takes_context=True)
def highlight_text(context,value,query):
    parts = match_parts(value,query,unicode_casefold=bool(context.get('activity')))
    return mark_safe(''.join(('<mark class="search-match">'+str(escape(text))+'</mark>') if matched else str(escape(text))
                             for text,matched in parts))
