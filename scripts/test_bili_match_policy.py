import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.bili_match_policy import resolve

class MatchTests(unittest.TestCase):
    def resolve(self,candidates,edition='book'):
        return resolve({'candidates':candidates},{'edition':edition,'source_category':'游戏剧本'},[{'id':c['work_id'],'publisher_category':'游戏剧本'} for c in candidates])
    def candidate(self,**kw):
        return dict(work_id=1,name_match=True,author_match=True,edition='book',similarity=1,restricted=False,**kw)
    def test_exact_unique_auto_excluded(self):
        r=self.resolve([self.candidate()]);self.assertEqual(r['decision'],'auto_duplicate');self.assertEqual(r['disposition'],'exclude_duplicate')
    def test_author_alone_is_not_a_duplicate(self):
        c=self.candidate();c.update(name_match=False,similarity=.1)
        self.assertEqual(self.resolve([c])['decision'],'new_candidate')
    def test_similar_and_multiple_need_review(self):
        c=self.candidate();c.update(name_match=False,similarity=.8)
        self.assertEqual(self.resolve([c])['decision'],'manual_review')
        a=self.candidate();b={**a,'work_id':2}
        self.assertEqual(self.resolve([a,b])['decision'],'manual_review')
    def test_versions_and_restricted_never_auto_merge(self):
        c=self.candidate()
        self.assertEqual(self.resolve([c],'web')['decision'],'manual_review')
        self.assertEqual(self.resolve([c],'unknown')['decision'],'version_review')
        c['restricted']=True
        self.assertEqual(self.resolve([c])['decision'],'manual_review')

    def test_unknown_edition_without_match_is_new_candidate(self):
        r=self.resolve([],'unknown')
        self.assertEqual(r['decision'],'new_candidate')
        self.assertEqual(r['disposition'],'new_candidate')
        self.assertTrue(r['edition_needs_review'])

if __name__=='__main__':unittest.main()
