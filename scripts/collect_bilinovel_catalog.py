"""Resume a serial metadata-only queue. Never opens an application database."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
from urllib.request import build_opener
from urllib.robotparser import RobotFileParser
from lxml import html

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_wenku8 as net
from scripts.bili_editions import apply as classify_edition
net.AGENT = 'SharedBooklist-MetadataProbe/0.1'
BASE = 'https://www.bilinovel.com'
PAGE = re.compile(r'/wenku/lastupdate_0_0_0_0_0_0_0_([1-9][0-9]*)_0\.html\Z')
DETAIL = re.compile(r'/novel/([1-9][0-9]*)\.html\Z')
MAX_PAGES, MAX_WORKS = 200, 6000

class Pause(RuntimeError): pass

def transient_transport(record):
    return (record.get('status') in (None,200) and record.get('result') == 'transport_failed'
            and (record.get('cause_class') or record.get('error_class')) in {
                'SSLEOFError', 'TimeoutError', 'ConnectionResetError',
                'ConnectionAbortedError', 'RemoteDisconnected', 'IncompleteRead'})

def detail_action(adapter, record, raw, bid):
    action=adapter.detail_action(record,raw)
    # A complete public detail can include Cloudflare's passive JS footer.
    # Accept it only when the existing strict parser validates the actual ID
    # and all required metadata. Never interact with a challenge or retry HTTP blocks.
    if action=='skip_challenge' and record.get('status')==200 and raw:
        try:adapter.extract(raw,bid)
        except ValueError:return action
        return 'parse'
    return action

def stamp(): return datetime.now(timezone.utc).isoformat()
def sha(raw): return hashlib.sha256(raw).hexdigest()
def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)

def connect(out):
    db = sqlite3.connect(out/'queue.sqlite3', timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('pragma journal_mode=wal')
    db.executescript('''
    create table if not exists pages(number integer primary key,url text unique not null,status text not null default 'pending',sha256 text,error text);
    create table if not exists works(book_id integer primary key,title_hint text not null default '',page integer,status text not null default 'pending',metadata_json text,sha256 text,error text,finished_at text);
    create table if not exists requests(id integer primary key,at text not null,kind text not null,url text not null,record_json text not null);
    ''')
    db.commit()
    return db

def parse_page(raw, number, limit):
    tree = html.fromstring(raw)
    if tree.xpath('//div[@id="pagelink"]/strong/text()') != [str(number)]:
        raise Pause('listing_identity_changed')
    anchors = tree.xpath('//li[contains(concat(" ",normalize-space(@class)," ")," book-li ")]/a[@class="book-layout"]')
    if not 1 <= len(anchors) <= 30: raise Pause('listing_structure_changed')
    works = []
    for node in anchors:
        match = DETAIL.fullmatch(node.get('href', ''))
        title = node.xpath('.//h4[contains(@class,"book-title")]/text()')
        if not match or len(title) != 1 or not title[0].strip(): raise Pause('listing_item_invalid')
        works.append((int(match[1]), title[0].strip()))
    pages = []
    for link in tree.xpath('//div[@id="pagelink"]/a/@href'):
        match = PAGE.fullmatch(link)
        if not match: raise Pause('listing_pagination_changed')
        page = int(match[1])
        if not 1 <= page <= limit: raise Pause('listing_scope_grew_requires_review')
        pages.append((page, BASE+link))
    return works, pages

def save_page(db, out, raw, number, limit):
    works, pages = parse_page(raw, number, limit)
    (out/'pages'/f'{number}.html').write_bytes(raw)
    with db:
        for page, url in pages: db.execute('insert or ignore into pages(number,url) values(?,?)', (page, url))
        for bid, title in works: db.execute('insert or ignore into works(book_id,title_hint,page) values(?,?,?)', (bid, title, number))
        if db.execute('select count(*) from works').fetchone()[0] > MAX_WORKS: raise Pause('work_scope_limit')
        db.execute('update pages set status="parsed",sha256=?,error=null where number=?', (sha(raw), number))

def helper(out):
    spec = importlib.util.spec_from_file_location('frozen_bili_adapter', out/'adapter.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def initialize_from_saved(out):
    if out.exists(): raise ValueError('output_already_exists_use_existing_queue')
    first = ROOT/'.runtime/bili_first20_20261023'
    speed = ROOT/'.runtime/bili_speed10_20261023'
    last = json.loads((first/'catalog-size-check.json').read_text(encoding='utf-8'))
    limit = last['last_page_number']
    if not 1 <= limit <= MAX_PAGES: raise ValueError('invalid_page_limit')
    out.mkdir();(out/'pages').mkdir();(out/'details').mkdir()
    shutil.copyfile(first/'collect.py', out/'adapter.py')
    config = {'version':1,'created_at':stamp(),'page_limit':limit,'max_works':MAX_WORKS,'extra_wait_seconds':3,
              'concurrency':1,'adapter_sha256':sha((out/'adapter.py').read_bytes()),
              'worker_sha256':sha(Path(__file__).read_bytes()),'catalog_estimate':last['estimated_listing_entries'],
              'scope':'detail metadata only; no novel text, image downloads, application DB writes or duplicate merging'}
    atomic_json(out/'config.json', config)
    db = connect(out)
    with db:
        db.execute('insert into pages(number,url) values(1,?)', (BASE+'/wenku/',))
        db.execute('insert into pages(number,url) values(?,?)', (limit, last['request']['url']))
    save_page(db, out, (first/'listing.html').read_bytes(), 1, limit)
    save_page(db, out, (first/'lastpage-count.html').read_bytes(), limit, limit)
    adapter = helper(out)
    sources = [(first, json.loads((first/'state.json').read_text(encoding='utf-8'))['items']),
               (speed, json.loads((speed/'state.json').read_text(encoding='utf-8'))['items'])]
    probe = ROOT/'.runtime/bili_probe_20261023'
    sources.append((probe, {str(row['bili_id']):row for row in json.loads((probe/'comparison.json').read_text(encoding='utf-8'))['items']}))
    with db:
        for directory, entries in sources:
            for value, old in entries.items():
                bid = int(value);raw = (directory/f'{bid}.html').read_bytes();item = adapter.extract(raw,bid)
                item['collected_at'] = old.get('collected_at') or stamp()
                item['collection_origin'] = 'reused_saved_public_detail'
                item['decision'] = 'deferred_review';item['previous_decision'] = old.get('decision')
                (out/'details'/f'{bid}.html').write_bytes(raw)
                db.execute('insert or ignore into works(book_id,title_hint) values(?,?)',(bid,item['title']))
                db.execute('update works set status="parsed",metadata_json=?,sha256=?,finished_at=? where book_id=?',
                           (json.dumps(item,ensure_ascii=False),sha(raw),item['collected_at'],bid))
        skips = json.loads((first/'state.json').read_text(encoding='utf-8')).get('skipped',{})
        for value, row in skips.items():
            bid = int(value)
            db.execute('insert or ignore into works(book_id,title_hint) values(?,?)',(bid,row.get('title','')))
            db.execute('update works set status="skipped",error=?,finished_at=? where book_id=?',
                       (row['reason'],row.get('at') or stamp(),bid))
    db.close()
    return config

def initialize(out,seed_existing=False):
    if seed_existing:return initialize_from_saved(out)
    if out.exists():raise ValueError('output_already_exists_use_existing_queue')
    out.mkdir();(out/'pages').mkdir();(out/'details').mkdir()
    shutil.copyfile(ROOT/'scripts/bili_metadata.py',out/'adapter.py')
    config={'version':1,'created_at':stamp(),'page_limit':MAX_PAGES,'max_works':MAX_WORKS,
            'extra_wait_seconds':3,'concurrency':1,'catalog_estimate':0,'discover_page_limit':True,
            'adapter_sha256':sha((out/'adapter.py').read_bytes()),'worker_sha256':sha(Path(__file__).read_bytes()),
            'scope':'detail metadata only; no novel text, image downloads, application DB writes or duplicate merging'}
    atomic_json(out/'config.json',config)
    db=connect(out)
    with db:db.execute('insert into pages(number,url) values(1,?)',(BASE+'/wenku/',))
    db.close()
    return config

@contextmanager
def worker_lock(out):
    import msvcrt
    with (out/'worker.lock').open('a+b') as stream:
        stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
        msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        try: yield
        finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)

class Worker:
    def __init__(self, out):
        self.out = out
        self.config = json.loads((out/'config.json').read_text(encoding='utf-8'))
        if sha((out/'adapter.py').read_bytes()) != self.config['adapter_sha256'] or sha(Path(__file__).read_bytes()) != self.config['worker_sha256']:
            raise Pause('worker_or_adapter_changed')
        self.db = connect(out);self.adapter = helper(out)
        self.opener = build_opener(net.NoRedirect());self.rules = None;self.delay = 3
        self.last_finished = 0;self.last_policy = 0;self.phase = 'starting'
        self.state = {'status':'running','started_at':stamp(),'pid':os.getpid(),'concurrency':1,'business_database_writes':False}

    def checkpoint(self, **updates):
        self.state.update(updates,updated_at=stamp(),phase=self.phase,extra_wait_seconds=self.delay)
        self.state['pages'] = dict(self.db.execute('select status,count(*) from pages group by status'))
        self.state['works'] = dict(self.db.execute('select status,count(*) from works group by status'))
        self.state['requests_this_queue'] = self.db.execute('select count(*) from requests').fetchone()[0]
        self.state['discovered_unique'] = self.db.execute('select count(*) from works').fetchone()[0]
        atomic_json(self.out/'state.json',self.state)

    def request(self, url, limit, kind):
        for attempt in range(3):
            wait = max(self.delay, (0, 10, 30)[attempt])
            while time.monotonic()-self.last_finished < wait:
                if (self.out/'STOP').exists(): raise Pause('user_stop_marker')
                time.sleep(max(0, min(.5, wait-(time.monotonic()-self.last_finished))))
            if (self.out/'STOP').exists(): raise Pause('user_stop_marker')
            if self.rules is not None and not self.rules.can_fetch(net.AGENT,url): raise Pause('robots_disallowed')
            self.checkpoint(active_url=url, retry_attempt=attempt, retry_wait_seconds=0)
            record,raw = net.fetch(url,limit,self.opener);self.last_finished=time.monotonic()
            record['attempt']=attempt+1
            with self.db:self.db.execute('insert into requests(at,kind,url,record_json) values(?,?,?,?)',
                                       (stamp(),kind,url,json.dumps(record)))
            if not transient_transport(record) or attempt==2:
                self.checkpoint(retry_attempt=0, retry_wait_seconds=0)
                return record,raw
            self.checkpoint(retry_attempt=attempt+1,retry_wait_seconds=(10,30)[attempt])

    def policy(self):
        record,raw = self.request(BASE+'/robots.txt',65536,'robots')
        if raw is None or record.get('status')!=200 or record.get('media_type')!='text/plain':raise Pause('robots_unavailable')
        try:text=raw.decode('utf-8-sig')
        except UnicodeError:raise Pause('robots_encoding_unknown')
        if '<' in text or '\x00' in text:raise Pause('robots_format_unknown')
        allowed={'user-agent','allow','disallow','sitemap','crawl-delay','request-rate','host','content-signal'}
        for line in text.splitlines():
            line=line.split('#',1)[0].strip()
            if line and (':' not in line or line.partition(':')[0].lower().strip() not in allowed):raise Pause('robots_format_unknown')
        rules=RobotFileParser();rules.parse(text.splitlines());rate=rules.request_rate(net.AGENT)
        self.delay=max(3,rules.crawl_delay(net.AGENT) or 0,rate.seconds/rate.requests if rate else 0)
        if self.delay>60:raise Pause('robots_delay_requires_schedule')
        (self.out/'robots.txt').write_bytes(raw);self.rules=rules;self.last_policy=time.monotonic()

    def listing(self, row):
        record,raw = self.request(row['url'],524288,'listing')
        action=self.adapter.detail_action(record,raw)
        if action=='skip_challenge' and record.get('status')==200 and raw:
            try:parse_page(raw,row['number'],self.config['page_limit'])
            except Pause:pass
            else:action='parse'
        if action.startswith('skip_'):
            with self.db:self.db.execute('update pages set status="skipped",error=? where number=?',(action,row['number']))
            return
        if action!='parse' or record.get('media_type')!='text/html':raise Pause('listing_'+action)
        save_page(self.db,self.out,raw,row['number'],self.config['page_limit'])
        if row['number']==1 and self.config.get('discover_page_limit'):
            _,pages=parse_page(raw,1,self.config['page_limit'])
            if not pages:raise Pause('listing_last_page_unknown')
            self.config.update(page_limit=max(n for n,_ in pages),discover_page_limit=False)
            self.config['catalog_estimate']=self.config['page_limit']*30
            atomic_json(self.out/'config.json',self.config)

    def detail(self, row):
        bid=row['book_id'];url=f'{BASE}/novel/{bid}.html'
        record,raw=self.request(url,524288,'detail');action=detail_action(self.adapter,record,raw,bid)
        if action.startswith('skip_'):
            with self.db:self.db.execute('update works set status="skipped",error=?,finished_at=? where book_id=?',(action,stamp(),bid))
            return
        if action!='parse':raise Pause(action)
        if record.get('media_type')!='text/html':raise Pause('detail_media_unknown')
        (self.out/'details'/f'{bid}.html').write_bytes(raw)
        try:item=self.adapter.extract(raw,bid)
        except ValueError as error:
            code=str(error)
            if code in ('source_id_mismatch','identity_conflict'):raise Pause(code)
            status='skipped' if code=='restriction_page' else 'parse_review'
            with self.db:self.db.execute('update works set status=?,sha256=?,error=?,finished_at=? where book_id=?',
                                        (status,sha(raw),code,stamp(),bid))
            return
        classify_edition(item)
        item.update(decision='deferred_review',collection_origin='catalog_queue')
        with self.db:self.db.execute('update works set status="parsed",metadata_json=?,sha256=?,finished_at=? where book_id=?',
            (json.dumps(item,ensure_ascii=False),sha(raw),stamp(),bid))

    def run(self):
        try:
            self.checkpoint();self.policy()
            self.phase='discovering_directory';self.checkpoint()
            while row:=self.db.execute('select * from pages where status="pending" order by number limit 1').fetchone():
                if time.monotonic()-self.last_policy>3600:self.policy()
                self.listing(row);self.checkpoint()
                print(json.dumps({'phase':self.phase,'page':row['number'],'discovered':self.state['discovered_unique']}),flush=True)
            coverage=self.db.execute('select count(*) from pages where status="parsed"').fetchone()[0]==self.config['page_limit']
            self.phase='collecting_details';self.checkpoint(directory_frozen_at=stamp(),directory_coverage_complete=coverage)
            while row:=self.db.execute('select * from works where status="pending" order by book_id limit 1').fetchone():
                if time.monotonic()-self.last_policy>3600:self.policy()
                self.detail(row);self.checkpoint()
                if sum(self.state['works'].get(k,0) for k in ('parsed','skipped','parse_review'))%20==0:
                    print(json.dumps({'phase':self.phase,'works':self.state['works']}),flush=True)
            self.phase='finished'
            if coverage:self.checkpoint(status='completed_with_review',finished_at=stamp())
            else:self.checkpoint(status='needs_attention',reason='directory_partial_listing_restricted',stopped_at=stamp())
        except KeyboardInterrupt:self.checkpoint(status='interrupted',reason='keyboard_interrupt')
        except Exception as error:
            self.checkpoint(status='needs_attention',reason=str(error) if isinstance(error,Pause) else type(error).__name__,stopped_at=stamp())
            print(json.dumps({'status':'needs_attention','reason':self.state['reason']}),flush=True)
        finally:self.db.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT/'.runtime/bili_catalog_20261023')
    parser.add_argument('--initialize',action='store_true')
    parser.add_argument('--seed-existing',action='store_true',help='Reuse this machine\'s historical October 23 trial snapshots; optional')
    parser.add_argument('--resume-reviewed',action='store_true')
    args=parser.parse_args();out=args.directory.resolve()
    if out.parent!=(ROOT/'.runtime').resolve() or out.is_symlink():parser.error('output must be an immediate .runtime directory')
    if args.seed_existing and not args.initialize:parser.error('--seed-existing requires --initialize')
    if args.initialize:initialize(out,args.seed_existing)
    state=out/'state.json'
    if state.exists():
        previous=json.loads(state.read_text(encoding='utf-8'))
        if previous['status']=='needs_attention' and not args.resume_reviewed:parser.error('review stopped checkpoint before resume')
        if previous['status'].startswith('completed'):return
    with worker_lock(out):Worker(out).run()

if __name__=='__main__':main()
