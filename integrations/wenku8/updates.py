"""Parse only observed book links and dated cards in the authenticated update list."""
from datetime import date
import re
from urllib.parse import urljoin, urlsplit, parse_qs
from .session import BASE, LISTING, SourceStop, allowed_url


def listing_page(root, today):
    entries = {}
    for link in root.xpath('//a[@href]'):
        url = urljoin(BASE, link.get('href'))
        parsed = urlsplit(url)
        match = re.fullmatch(r'/book/([1-9][0-9]{0,7})\.htm', parsed.path)
        if not match or parsed.netloc != 'www.wenku8.net':
            continue
        # Old source pages sometimes contain http book links: normalize only this
        # exact host/path. Never follow a source-supplied alternate origin.
        url = BASE+parsed.path
        allowed_url(url)
        book_id = int(match[1])
        if book_id in entries:
            continue
        for parent in link.iterancestors():
            identities = {urlsplit(urljoin(BASE, a.get('href'))).path
                          for a in parent.xpath('.//a[@href]')
                          if re.fullmatch(r'/book/[1-9][0-9]{0,7}\.htm', urlsplit(urljoin(BASE, a.get('href'))).path)}
            dates = set(re.findall(r'(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)', parent.text_content()))
            if len(identities) > 1:
                break
            if len(dates) != 1:
                continue
            updated = date.fromisoformat(next(iter(dates)))
            if updated > today:
                raise SourceStop('source_listing_future_date')
            titles = parent.xpath('.//a[b]')
            title = ' '.join((titles[0] if titles else link).text_content().split())
            if not title or len(title) > 500:
                raise SourceStop('source_listing_title_changed')
            entries[book_id] = {'book_id': book_id, 'source_url': url, 'title': title, 'date': updated.isoformat()}
            break
        else:
            raise SourceStop('source_listing_card_changed')
        if book_id not in entries:
            raise SourceStop('source_listing_card_changed')
    values = list(entries.values())
    if not values or len(values) > 100 or any(a['date'] < b['date'] for a, b in zip(values, values[1:])):
        raise SourceStop('source_listing_order_changed')
    pages = {}
    for link in root.xpath('//a[@href]'):
        url = urljoin(BASE, link.get('href'))
        try:
            allowed_url(url)
        except SourceStop:
            continue
        if urlsplit(url).path != urlsplit(LISTING).path:
            continue
        query = parse_qs(urlsplit(url).query)
        page = int(query.get('page', ['1'])[0])
        pages[page] = url
    if not pages:
        raise SourceStop('source_pagination_unrecognized')
    return values, pages


def discover(client, cutoff, today, *, max_pages=20, max_items=200):
    """Require an observed next-page link and a complete date boundary before writes."""
    result = {}
    previous_date = None
    for number in range(1, max_pages+1):
        rows, pages = listing_page(client.listing(number), today)
        if previous_date is not None and rows[0]['date'] > previous_date:
            raise SourceStop('source_listing_changed_during_scan')
        previous_date = rows[-1]['date']
        for row in rows:
            if row['date'] >= cutoff.isoformat():
                old = result.get(row['book_id'])
                if old and old['date'] != row['date']:
                    raise SourceStop('source_listing_changed_during_scan')
                result[row['book_id']] = row
                if len(result) > max_items:
                    raise SourceStop('source_discovery_item_budget_reached')
        if rows[-1]['date'] < cutoff.isoformat() or not any(page > number for page in pages):
            return list(result.values())
        if number+1 not in pages:
            raise SourceStop('source_next_page_not_observed')
    raise SourceStop('source_discovery_page_budget_reached')
