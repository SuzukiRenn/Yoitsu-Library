"""Offline edition and review checks; no business database or network access."""
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.bili_editions import classify, import_edition, display_label, apply
from scripts.bili_review_server import Dashboard

class EditionTests(unittest.TestCase):
    def test_confirmed_supplement_preserves_raw_source(self):
        item=apply({'edition':'unknown','source_category':'','tags':['日本轻小说'],'publisher_supplement':{'value':'小学馆','confirmed':True,'work_id':1325}})
        self.assertEqual(item['source_category'],'')
        self.assertEqual(item['edition_label'],'小学馆')
        item=apply({'edition':'unknown','source_category':'','tags':['日本轻小说'],'publisher_supplement':{'value':'小学馆','confirmed':False}})
        self.assertEqual(item['edition'],'unknown')
    def test_publisher_label_preserved_without_claiming_bunko_format(self):
        for category in ('小学馆','集英社','一迅社','讲谈社'):
            item=apply({'edition':'unknown','source_category':category,'tags':['日本轻小说',category]})
            self.assertEqual(item['edition'],'book')
            self.assertEqual(item['edition_label'],category)
            self.assertEqual(display_label('book',category),category)
        self.assertEqual(display_label('book','电击文库'),'文库版')
        self.assertEqual(display_label('web','集英社'),'Web版')
        self.assertEqual(classify('unknown','集英社',['Web轻小说']),'unknown')
        self.assertEqual(classify('unknown','不认识出版社',['日本轻小说']),'unknown')
    def test_unknown_defaults_to_other_only_at_import(self):
        self.assertEqual(import_edition('unknown'),'other')
        self.assertEqual(import_edition('other'),'other')
        self.assertEqual(import_edition('web'),'web')
        self.assertEqual(import_edition('book'),'book')
        self.assertEqual(classify('unknown','未核实分类'),'unknown')
    def test_explicit_other_and_unknown_are_distinct(self):
        self.assertEqual(classify('unknown','游戏剧本',['日本轻小说']),'other')
        self.assertEqual(classify('unknown','未核实分类'),'unknown')
        self.assertEqual(classify('unknown','游戏剧本',['Web轻小说']),'unknown')
        self.assertEqual(classify('book','电击文库'),'book')
        self.assertEqual(classify('web','Web轻小说'),'web')

    def test_other_review_and_cross_type_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            app=Dashboard.__new__(Dashboard);app.directory=Path(folder);app.review=Path(folder)/'review.sqlite3'
            app.adapter=SimpleNamespace(stamp=lambda:'test')
            app.target=lambda wid:{'edition':'other','restricted':False}
            with sqlite3.connect(Path(folder)/'queue.sqlite3') as db:
                db.execute('create table works(book_id integer,status text)');db.execute('insert into works values(340,"parsed")')
            db.close()
            with sqlite3.connect(app.review) as db:
                db.executescript('create table decisions(book_id primary key,action,edition,target_work_id,target_edition,note,updated_at); create table audit(book_id,before_json,after_json,created_at);')
            db.close()
            payload={'book_id':340,'action':'duplicate','edition':'other','target_work_id':1,'target_edition':'other'}
            self.assertEqual(app.decide(payload)['edition'],'other')
            with self.assertRaisesRegex(ValueError,'类型不同'):app.decide({**payload,'edition':'book'})
            with self.assertRaisesRegex(ValueError,'仅用于Web版'):app.decide({**payload,'action':'related_version'})
            self.assertEqual(app.decide({**payload,'action':'independent'})['action'],'independent')

if __name__=='__main__':unittest.main()
