"""Shared navigation for public and activity shelves; API paging stays unchanged."""
PAGE_SIZE = 20


def navigation(result, link):
    current, size, total = result['page'], result['page_size'], result['total']
    count = max(1, (total + size - 1) // size)
    numbers = sorted({1, count, *range(max(1, current-2), min(count, current+2)+1)})
    links = []
    for number in numbers:
        if links and number - links[-1]['number'] > 1:
            links.append({'number': number-1, 'gap': True})
        links.append({'number': number, 'url': link(number), 'current': number == current})
    return {'current': current, 'count': count, 'links': links,
        'previous': link(current-1) if current > 1 else None,
        'next': link(current+1) if current < count else None,
        'last_url': link(count)}
