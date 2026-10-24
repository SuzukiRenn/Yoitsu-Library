"""Read-only ordered IDs from the same shelf query; never restore unavailable entries."""
from urllib.parse import parse_qs, urlsplit, urlencode
from uuid import uuid4, UUID
from django.core.exceptions import ValidationError


def context(request, work_id, source):
    raw = request.GET.get('from', '')
    if not raw or source != raw:
        return None
    activity = request.GET.get('activity')
    token = request.GET.get('browse')
    if token:
        try:
            if str(UUID(token)) != token:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValidationError('浏览上下文无效') from None
    else:
        token = str(uuid4())
    values = parse_qs(urlsplit(source).query, keep_blank_values=True)
    from .shelf_filters import personal_filters
    personal=personal_filters({name:items[0] for name,items in values.items()})
    filters = {'client':request.anonymous_client, **personal, 'tags': values.get('tag', []), 'tag_mode': values.get('tag_mode', ['all'])[0], 'sort': values.get('sort', ['recommendations'])[0], 'q': values.get('q', [''])[0], 'ids_only': True}
    if activity:
        from .activities import listing
        ids = listing(int(activity), **filters)
    else:
        from .public_booklist import listing
        ids = listing(period=values.get('period', ['all'])[0], **filters)
    def link(pk):
        params = ([('activity', activity)] if activity else []) + [('from', source), ('browse', token)]
        return f'/works/{pk}/?' + urlencode(params)
    position = ids.index(work_id) if work_id in ids else None
    return {'id': token, 'source': source, 'activity': activity or '', 'current': work_id, 'ids': ids,
        'previous': link(ids[position-1]) if position is not None and position > 0 else None,
        'next': link(ids[position+1]) if position is not None and position+1 < len(ids) else None,
        'available': position is not None, 'position': position+1 if position is not None else None, 'total': len(ids)}
