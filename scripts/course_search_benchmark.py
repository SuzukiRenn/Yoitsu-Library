"""Compare normal lookup and deterministic intent fixtures on the synthetic demo."""
import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.conf import settings
from django.test import Client, override_settings
from booklist.models import Work
from booklist.public_booklist import effective_tags

CASES = [
    # Query, manually selected shelf keyword/tag, mock intent and expected title.
    ('山间来信', '山间来信', '', ['山间来信'], [], ['山间来信']),
    ('给我找有奇幻色彩、发生在图书馆的作品', '图书馆', '奇幻', ['图书馆'], ['奇幻'], ['月光图书馆']),
    ('想读森林和自然题材', '森林', '自然', ['森林'], ['自然'], ['在森林尽头']),
    ('推荐一本沿河旅行的书', '河流', '旅行', ['河流'], ['旅行'], ['沿着河流读下去']),
    ('请找讲火星火车的故事', '火星火车', '', ['火星火车'], [], []),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Make one real API call per question; may incur cost.')
    options = parser.parse_args()
    demo = (settings.BASE_DIR / '.runtime').resolve()
    if settings.ENV != 'development' or demo not in settings.DATA_DIR.resolve().parents:
        parser.error('Use an independent development demo data directory.')
    if Work.objects.count() != 4 or Work.objects.filter(source_metadata__identity_demo=True).count() != 4:
        parser.error('Run only on the four-work seed_identity_demo dataset.')
    if options.live and not settings.COURSE_DEEPSEEK_KEY:
        parser.error('A locally supplied COURSE_DEEPSEEK_KEY is required for --live.')
    import re
    from html import unescape
    browser = Client(HTTP_HOST='localhost')
    results = []
    for query, shelf_word, shelf_tag, keywords, tags, expected in CASES:
        def search(mode):
            response = browser.post('/find-books/', {'mode': mode, 'query': query}, follow=True)
            if response.status_code != 200:
                raise RuntimeError(f'Unexpected HTTP {response.status_code}')
            html = response.content.decode()
            titles = re.findall(r'<h3 id="find-result-[0-9]+">([^<]+)</h3>', html)
            return {'top3': [unescape(title) for title in titles[:3]],
                    'fallback': '已执行普通检索' in html}
        full_sentence = search('plain')
        filters = {'q': shelf_word}
        if shelf_tag:
            tag_id = effective_tags().filter(tag__name=shelf_tag).values_list('tag_id', flat=True).first()
            if tag_id is None:
                raise RuntimeError(f'Synthetic tag missing: {shelf_tag}')
            filters['tag'] = tag_id
        shelf_page = browser.get('/', filters)
        if shelf_page.status_code != 200:
            raise RuntimeError(f'Unexpected public shelf HTTP {shelf_page.status_code}')
        # The shelf API serves the same filter contract as the actual controls above.
        shelf_api = browser.get('/api/v1/public/works/', filters)
        if shelf_api.status_code != 200:
            raise RuntimeError(f'Unexpected public shelf API HTTP {shelf_api.status_code}')
        shelf = {'keyword': shelf_word, 'tag': shelf_tag, 'top3': [row['title'] for row in shelf_api.json()['results'][:3]]}
        if options.live:
            enhanced = search('ai')
        else:
            with override_settings(COURSE_DEEPSEEK_KEY='synthetic-fixture'), patch(
                    'booklist.course_find.extract', return_value={'keywords':keywords, 'tags':tags, 'exclude':[]}), patch('booklist.course_find.reserve_model_budget'):
                enhanced = search('ai')
        results.append({'query': query, 'expected': expected, 'full_sentence': full_sentence, 'manual_shelf': shelf, 'enhanced': enhanced})
    answerable = [row for row in results if row['expected']]
    no_answer = [row for row in results if not row['expected']]
    print(json.dumps({'model_mode': 'live_api' if options.live else 'mock_intent',
        'question_count': len(results), 'answerable': len(answerable),
        'enhanced_parsed': sum(not row['enhanced']['fallback'] for row in results),
        'enhanced_fallbacks': sum(row['enhanced']['fallback'] for row in results),
        'top3_hits': {mode: sum(not row[mode].get('fallback') and bool(set(row['expected']) & set(row[mode]['top3']))
                               for row in answerable) for mode in ('full_sentence', 'manual_shelf', 'enhanced')},
        'interpretation': 'Mock intents verify retrieval and do not measure model quality or prove a retrieval improvement.',
        'no_answer_cases': no_answer, 'cases': results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
