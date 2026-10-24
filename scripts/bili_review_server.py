"""Loopback-only collection dashboard; decisions are staging records, never merges."""
import argparse
from contextlib import contextmanager
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time
from urllib.parse import parse_qs,urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.bili_editions import classify, apply as classify_edition, import_edition, display_label, effective_category
from scripts.bili_match_policy import resolve as resolve_match, VERSION as MATCH_VERSION
CATALOG=ROOT/'.runtime/acceptance_preview_be33cfd5b19344cea0350d5fb0a3c2ef/booklist.sqlite3'
STATIC=ROOT/'scripts/bili_review_ui'

@contextmanager
def connect(path,readonly=False):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro' if readonly else path,uri=readonly,timeout=10)
    db.row_factory=sqlite3.Row
    try:
        with db:yield db
    finally:db.close()

def catalog_baseline(catalog):
    from scripts.bili_metadata import BOOK_CATEGORIES
    with connect(catalog,True) as db:
        db.execute('begin')
        rows=[]
        for source in db.execute('select id,wenku8_book_id,wenku8_title,original_submitted_title,title_override,wenku8_author,author_override,source_metadata,hidden_at,merged_into_id,review_status from booklist_work'):
            row=dict(source);metadata=json.loads(row.pop('source_metadata') or '{}')
            row['publisher_category']=metadata.get('publisher_category')
            row['edition']='book' if row['publisher_category'] in BOOK_CATEGORIES else 'unknown'
            row['names']=list(filter(None,[row['wenku8_title'],row['original_submitted_title'],row['title_override']]))
            row['authors']=list(filter(None,[row['wenku8_author'],row['author_override']]))
            rows.append(row)
        byid={r['id']:r for r in rows}
        for wid,alias in db.execute('select work_id,alias from booklist_workalias where archived_at is null'):
            if wid in byid:byid[wid]['names'].append(alias)
        return rows

class Dashboard:
    def __init__(self,directory,catalog=CATALOG,baseline_path=None):
        self.directory=directory;self.token=secrets.token_urlsafe(32);self.matcher_error=None
        self.catalog=catalog
        self.review=directory/'review.sqlite3'
        with connect(self.review) as db:
            db.execute('pragma journal_mode=wal')
            db.executescript('''
            create table if not exists matches(book_id integer primary key,result_json text not null,updated_at text not null);
            create table if not exists decisions(book_id integer primary key,action text not null,edition text not null,target_work_id integer,target_edition text,note text not null,updated_at text not null);
            create table if not exists audit(id integer primary key,book_id integer not null,before_json text,after_json text not null,created_at text not null);
            ''')
        spec=importlib.util.spec_from_file_location('review_adapter',directory/'adapter.py')
        self.adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.adapter)
        self.adapter.key=lru_cache(maxsize=100000)(self.adapter.key)
        baseline_file=baseline_path or (ROOT/'.runtime/bili_first20_20261023/catalog-baseline.json' if catalog.resolve()==CATALOG.resolve() else directory/'catalog-baseline.json')
        if baseline_file.is_file():baseline=json.loads(baseline_file.read_text(encoding='utf-8'))
        elif baseline_path:raise ValueError('baseline_file_not_found')
        else:baseline={'rows':catalog_baseline(catalog)}
        self.rows=baseline['rows']
        for row in self.rows:row['edition']=classify(row['edition'],row.get('publisher_category'))
        self.byid={r['id']:r for r in self.rows}

    def target(self,wid):
        item=self.byid.get(wid)
        if not item:return None
        with connect(self.catalog,True) as db:
            row=db.execute('select wenku8_title,original_submitted_title,wenku8_author,wenku8_description,hidden_at,merged_into_id,review_status from booklist_work where id=?',(wid,)).fetchone()
        if not row:return None
        return {'work_id':wid,'title':row['wenku8_title'] or row['original_submitted_title'],'author':row['wenku8_author'] or '',
            'description':row['wenku8_description'] or '', 'wenku8_id':item['wenku8_book_id'],'edition':item['edition'],
            'edition_label':display_label(item['edition'],item['publisher_category']),'publication_label':display_label('book',item['publisher_category']),
            'category':item['publisher_category'],'restricted':bool(row['hidden_at'] or row['merged_into_id'] or row['review_status']!='clear')}

    def match_loop(self):
        while True:
            try:
                with connect(self.review,True) as db:done={r[0] for r in db.execute('select book_id from matches where json_extract(result_json,\'$.policy_version\')=?',(MATCH_VERSION,))}
                with connect(self.directory/'queue.sqlite3',True) as db:
                    rows=db.execute('select book_id,metadata_json from works where status="parsed" order by book_id').fetchall()
                for row in rows:
                    if row['book_id'] in done:continue
                    item=classify_edition(json.loads(row['metadata_json']));result=self.adapter.compare(item,self.rows)
                    value=resolve_match(result,item,self.rows)
                    with connect(self.review) as db:db.execute('insert or replace into matches values(?,?,?)',
                        (row['book_id'],json.dumps(value,ensure_ascii=False),self.adapter.stamp()))
                    time.sleep(.02)
                self.matcher_error=None
            except Exception as e:self.matcher_error=type(e).__name__
            time.sleep(3)

    def progress(self):
        statefile=self.directory/'state.json'
        state=json.loads(statefile.read_text(encoding='utf-8')) if statefile.exists() else {'status':'starting'}
        with connect(self.directory/'queue.sqlite3',True) as db:
            state['works']=dict(db.execute('select status,count(*) from works group by status'))
            state['pages']=dict(db.execute('select status,count(*) from pages group by status'))
            last=db.execute('select finished_at from works where status="parsed" order by finished_at desc limit 1').fetchone()
            state['last_collected_at']=last[0] if last else None
        with connect(self.review,True) as db:
            state['match_counts']=dict(db.execute("select json_extract(result_json,'$.decision'),count(*) from matches group by 1"))
            state['decisions']=dict(db.execute('select action,count(*) from decisions group by action'))
            state['auto_duplicates']=db.execute("select count(*) from matches m where json_extract(result_json,'$.decision')='auto_duplicate' and not exists(select 1 from decisions d where d.book_id=m.book_id)").fetchone()[0]
        state['matcher_error']=self.matcher_error
        config=json.loads((self.directory/'config.json').read_text(encoding='utf-8'))
        state['page_limit']=config['page_limit'];state['catalog_estimate']=config['catalog_estimate']
        state['storage']=str(self.directory)
        return state

    def entries(self,kind,page,size):
        with connect(self.review,True) as db:
            matches={r['book_id']:json.loads(r['result_json']) for r in db.execute('select * from matches')}
            decisions={r['book_id']:dict(r) for r in db.execute('select * from decisions')}
        with connect(self.directory/'queue.sqlite3',True) as db:
            rows=db.execute('select book_id,title_hint,status,metadata_json,error from works where status!="pending" order by book_id desc').fetchall()
        chosen=[]
        for r in rows:
            mid=r['book_id'];match=matches.get(mid);decision=decisions.get(mid)
            reviewed=decision and decision['action']!='defer'
            automatic=match and match['decision']=='auto_duplicate' and not decision
            if kind=='review' and (reviewed or not match or (not decision and match['decision'] not in ('manual_review','version_review'))):continue
            if kind=='auto' and not automatic:continue
            if kind=='new' and (not match or match['decision'] not in ('new_candidate','new_version_candidate')):continue
            if kind=='unreviewed' and (reviewed or automatic or r['status']!='parsed'):continue
            if kind=='reviewed' and not reviewed:continue
            if kind=='skipped' and r['status']!='skipped':continue
            chosen.append((r,match,decision))
        result=[]
        for r,match,decision in chosen[(page-1)*size:page*size]:
            item=classify_edition(json.loads(r['metadata_json'])) if r['metadata_json'] else {'bili_id':r['book_id'],'title':r['title_hint'],'edition':'unknown'}
            targets=[]
            if match:
                for c in match['candidates']:
                    target=self.target(c['work_id'])
                    if target:targets.append({**target,'name_match':c['name_match'],'author_match':c['author_match'],'similarity':c['similarity']})
            if decision and decision['target_work_id'] and not any(t['work_id']==decision['target_work_id'] for t in targets):
                target=self.target(decision['target_work_id'])
                if target:targets.append(target)
            item.update(status=r['status'],error=r['error'],match=match,review=decision,targets=targets)
            item['disposition']=('exclude_duplicate' if decision['action']=='duplicate' else 'new_candidate' if decision['action']=='independent' else 'related_version' if decision['action']=='related_version' else 'pending_review') if decision else (match or {}).get('disposition','pending_review')
            item['import_edition']=import_edition(decision['edition'] if decision and decision['edition']!='unknown' else item['edition'])
            item['import_label']=display_label(item['import_edition'],effective_category(item))
            result.append(item)
        return {'items':result,'total':len(chosen),'page':page,'page_size':size}

    def search(self,query):
        key=self.adapter.key(query)
        if not key:return []
        found=[]
        for r in self.rows:
            if any(key in self.adapter.key(n) for n in r['names']+r['authors']):
                target=self.target(r['id'])
                if target:found.append(target)
            if len(found)==12:break
        return found

    def decide(self,data):
        bid=data.get('book_id');action=data.get('action');edition=data.get('edition','unknown')
        if type(bid)!=int or action not in ('duplicate','independent','related_version','defer') or edition not in ('book','web','other','unknown'):raise ValueError('审核参数无效')
        note=data.get('note','')
        if not isinstance(note,str) or len(note)>500:raise ValueError('备注最多500字')
        with connect(self.directory/'queue.sqlite3',True) as db:
            source=db.execute('select status from works where book_id=?',(bid,)).fetchone()
        if not source or source['status']!='parsed':raise ValueError('该条目尚未成功采集，不能确认')
        target_id=data.get('target_work_id');target_edition=data.get('target_edition','unknown')
        if action in ('duplicate','related_version'):
            if type(target_id)!=int:raise ValueError('请先选择右侧已有作品')
            target=self.target(target_id)
            if not target or target['restricted']:raise ValueError('目标作品不存在或处于受限状态，请稍后核对')
            actual=target['edition']
            if actual!='unknown':target_edition=actual
            if edition not in ('book','web','other') or target_edition not in ('book','web','other'):raise ValueError('请先明确两侧版本或类型')
            if action=='duplicate' and edition!=target_edition:raise ValueError('两侧版本或类型不同，不能确认成同一条目')
            if action=='related_version' and {edition,target_edition}!={'book','web'}:raise ValueError('另一版本关联仅用于Web版与文库版；其他类型请保留独立条目')
        else:target_id=None;target_edition=None
        new={'book_id':bid,'action':action,'edition':edition,'target_work_id':target_id,'target_edition':target_edition,
             'note':note,'updated_at':self.adapter.stamp()}
        with connect(self.review) as db:
            old=db.execute('select * from decisions where book_id=?',(bid,)).fetchone()
            db.execute('insert or replace into decisions values(:book_id,:action,:edition,:target_work_id,:target_edition,:note,:updated_at)',new)
            db.execute('insert into audit(book_id,before_json,after_json,created_at) values(?,?,?,?)',
                       (bid,json.dumps(dict(old),ensure_ascii=False) if old else None,json.dumps(new,ensure_ascii=False),new['updated_at']))
        return new

def handler(app,port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def valid_host(self):return self.headers.get('Host')==f'127.0.0.1:{port}'
        def send(self,status,raw,content='application/json; charset=utf-8'):
            if not isinstance(raw,bytes):raw=json.dumps(raw,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if not self.valid_host():return self.send(403,{'error':'invalid_host'})
            path=urlsplit(self.path);q=parse_qs(path.query)
            try:
                if path.path=='/':return self.send(200,(STATIC/'index.html').read_bytes().replace(b'__TOKEN__',app.token.encode()),'text/html; charset=utf-8')
                if path.path in ('/app.js','/style.css'):
                    return self.send(200,(STATIC/path.path[1:]).read_bytes(),'text/javascript; charset=utf-8' if path.path.endswith('.js') else 'text/css; charset=utf-8')
                if path.path=='/api/progress':return self.send(200,app.progress())
                if path.path=='/api/entries':return self.send(200,app.entries(q.get('filter',['review'])[0],max(1,int(q.get('page',['1'])[0])),12))
                if path.path=='/api/catalog':return self.send(200,app.search(q.get('q',[''])[0][:300]))
                return self.send(404,{'error':'not_found'})
            except Exception as e:return self.send(500,{'error':type(e).__name__})
        def do_POST(self):
            if not self.valid_host() or self.headers.get('Origin')!=f'http://127.0.0.1:{port}' or self.headers.get('X-Review-Token')!=app.token:
                return self.send(403,{'error':'请求校验失败，请刷新页面'})
            if self.path!='/api/decision':return self.send(404,{'error':'not_found'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192:raise ValueError('请求过大')
                data=json.loads(self.rfile.read(length));return self.send(200,app.decide(data))
            except ValueError as e:return self.send(400,{'error':str(e)})
            except Exception as e:return self.send(500,{'error':type(e).__name__})
    return Handler

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8899)
    parser.add_argument('--directory',type=Path,default=ROOT/'.runtime/bili_catalog_20261023')
    parser.add_argument('--catalog',type=Path,default=CATALOG,help='Existing local booklist SQLite; opened read-only')
    parser.add_argument('--baseline',type=Path,help='Optional public metadata baseline JSON')
    args=parser.parse_args();directory=args.directory.resolve()
    if directory.parent!=(ROOT/'.runtime').resolve() or not (directory/'queue.sqlite3').is_file():parser.error('invalid staging queue')
    if not args.catalog.is_file():parser.error('catalog SQLite not found; specify --catalog')
    app=Dashboard(directory,args.catalog,args.baseline);server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(app,args.port))
    threading.Thread(target=app.match_loop,daemon=True).start()
    print(f'BILI_REVIEW_READY http://127.0.0.1:{args.port}/',flush=True)
    server.serve_forever()

if __name__=='__main__':main()
