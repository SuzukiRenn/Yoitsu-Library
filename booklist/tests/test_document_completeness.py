"""Saved metadata plus explicitly injected HTML errors; no live requests."""
from pathlib import Path
import unittest
from integrations.wenku8.offline import extract, document_diagnostics


FIXTURE = Path(__file__).absolute().parents[2]/'tests/fixtures/wenku8/details/4151.htm'
URL = 'https://www.wenku8.net/book/4151.htm'


class DocumentCompletenessTests(unittest.TestCase):
    def test_unquoted_navigation_query_equals_does_not_invalidate_metadata(self):
        raw = '<html><body><a href=/modules/article/authorarticle.php?author=%D3%A3%C9%FA>author</a></body></html>'
        self.assertTrue(document_diagnostics(raw, [])['complete'])
        unsafe = raw.replace('%D3%A3%C9%FA', '%D3%A3&from=2')
        result = document_diagnostics(unsafe, [])
        self.assertFalse(result['complete'])
        self.assertEqual(result['ignored_href_entity_errors'], 0)

    def test_missing_native_diagnostics_do_not_relax_entity_policy(self):
        cases = [
            ('<a href="/?x=1&from=2">nav</a>', True, 1),
            ('<p>foo&from=2</p>', False, 0),
            ('''<div title=" href='/?x=1&from=2'">x</div>''', False, 0),
            ('<a href=/?x=1&from=2>x</a>', False, 0),
        ]
        for fragment, complete, ignored in cases:
            with self.subTest(fragment=fragment):
                result = document_diagnostics('<html><body>'+fragment+'</body></html>', [])
                self.assertEqual(result['complete'], complete)
                self.assertEqual(result['ignored_href_entity_errors'], ignored)

    def test_comments_raw_text_and_terminated_entities_are_not_missing_references(self):
        raw = '<html><body><!-- foo&from=2 --><script>var x="&from=2";</script><style>a{content:"&from=2"}</style><p>&amp; &#65; &#x41;</p></body></html>'
        result = document_diagnostics(raw, [])
        self.assertTrue(result['complete'])
        self.assertEqual(result['lexical_entity_error_count'], 0)

    def test_entity_diagnostic_limit_is_platform_independent_and_not_double_counted(self):
        for count, complete in ((50, True), (51, False)):
            with self.subTest(count=count):
                fragment = '<a href="/?x=1&from=2">x</a>' * count
                lexical = document_diagnostics('<html><body>'+fragment+'</body></html>', [])
                actual = self.inject(fragment)['document_diagnostics']
                self.assertEqual(lexical['complete'], complete)
                self.assertEqual(actual['complete'], complete)
                self.assertEqual(actual['ignored_href_entity_errors'], count)

    def inject(self, fragment):
        text = FIXTURE.read_text(encoding='utf-8')
        return extract(text.replace('<body>', '<body>'+fragment).encode('utf-8'), 4151, URL)

    def test_quoted_href_query_entity_noise_does_not_invalidate_metadata(self):
        expected = extract(FIXTURE.read_bytes(), 4151, URL)
        actual = self.inject('<div><a href="/index.php?x=1&from=2">nav</a></div>')
        self.assertEqual(actual['fields'], expected['fields'])
        self.assertTrue(actual['document_diagnostics']['complete'])
        self.assertGreater(actual['document_diagnostics']['ignored_href_entity_errors'], 0)

    def test_entity_error_in_actual_metadata_text_still_blocks(self):
        text = FIXTURE.read_text(encoding='utf-8').replace('作品Tags：穿越', '作品Tags：foo&from=2 穿越')
        data = extract(text.encode('utf-8'), 4151, URL)
        self.assertFalse(data['document_diagnostics']['complete'])
        self.assertIn('ERR_ENTITYREF_SEMICOL_MISSING', data['document_diagnostics']['blocking_error_types'])
        self.assertEqual(data['fields']['tags']['state'], 'parse_error')
        self.assertFalse(data['fields']['tags']['complete'])

    def test_href_spelling_inside_other_attribute_is_not_allowed(self):
        data = self.inject('''<div title=" href='/index.php?x=1&from=2'">x</div>''')
        self.assertFalse(data['document_diagnostics']['complete'])
        self.assertEqual(data['document_diagnostics']['ignored_href_entity_errors'], 0)

    def test_unquoted_href_remains_unverified(self):
        data = self.inject('<a href=/index.php?x=1&from=2>x</a>')
        self.assertFalse(data['document_diagnostics']['complete'])
        self.assertEqual(data['document_diagnostics']['ignored_href_entity_errors'], 0)

    def test_url_noise_does_not_hide_structural_error(self):
        data = self.inject('<a href="/index.php?x=1&from=2">x</a><div></span></div>')
        self.assertFalse(data['document_diagnostics']['complete'])
        self.assertTrue(data['document_diagnostics']['blocking_error_types'])

    def test_end_tag_strings_inside_script_are_not_real_endings(self):
        text = FIXTURE.read_text(encoding='utf-8').rsplit('</body>', 1)[0]
        data = extract((text+'<script>var x="</body></html>').encode('utf-8'), 4151, URL)
        self.assertFalse(data['document_diagnostics']['explicit_end_tags'])
        self.assertFalse(data['fields']['tags']['complete'])

    def test_missing_endings_remain_blocked_even_with_allowed_href_noise(self):
        text = FIXTURE.read_text(encoding='utf-8').replace('<body>', '<body><a href="/?x=1&from=2">x</a>')
        data = extract(text.rsplit('</body>', 1)[0].encode('utf-8'), 4151, URL)
        self.assertFalse(data['document_diagnostics']['complete'])
        self.assertFalse(data['fields']['tags']['complete'])

    def test_many_diagnostics_do_not_certify_collection(self):
        data = self.inject('<a href="/?x=1&from=2">x</a>' * 60)
        self.assertFalse(data['document_diagnostics']['complete'])
        self.assertIn('diagnostic_limit_exceeded', data['document_diagnostics']['blocking_error_types'])

    def test_diagnostics_never_echo_urls_or_parser_messages(self):
        import json
        data = self.inject('<a href="/?private=secret&from=2">x</a>')
        output = json.dumps(data['document_diagnostics'])
        self.assertNotIn('secret', output)
        self.assertNotIn('private', output)
