"""Resume bounded downloads of observed cover URLs; never modify the catalog database."""
import argparse
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler
from urllib.robotparser import RobotFileParser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import probe_wenku8 as transport


@contextmanager
def cache_lock(root):
    root.mkdir(parents=True,exist_ok=True)
    with (root/'.collector.lock').open('a+b') as stream:
        if stream.seek(0,2)==0:
            stream.write(b'0');stream.flush()
        stream.seek(0)
        if sys.platform=='win32':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform=='win32':
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(stream,fcntl.LOCK_UN)


def observed(database):
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)) as db:
        db.execute('PRAGMA query_only=ON')
        values = [r[0] for r in db.execute('SELECT CASE WHEN cover_url_overridden THEN cover_url_override ELSE wenku8_cover_url END FROM booklist_work ORDER BY public_added_at IS NULL,id')]
        values += [r[0] for r in db.execute('SELECT snapshot_cover_url FROM booklist_activitywork ORDER BY id')]
    return list(dict.fromkeys(v for v in values if isinstance(v, str) and v.startswith(('http://','https://'))))


def policy_for(origin, opener, fetch=transport.fetch, sleeper=time.sleep):
    attempts=[]
    for attempt in range(2):
        if attempt:sleeper(10)
        record,raw=fetch(origin+'/robots.txt',65536,opener)
        attempts.append(record)
        if raw is not None or record.get('status') is not None:break
    if record.get('status')==404:
        text=''
    elif raw is not None and record.get('media_type')=='text/plain':
        text=raw.decode('utf-8-sig')
    else:
        return None, {'status':'policy_unavailable','attempts':attempts}
    policy=RobotFileParser();policy.parse(text.splitlines())
    rate=policy.request_rate(transport.AGENT)
    delay=max(2,policy.crawl_delay(transport.AGENT) or 0,rate.seconds/rate.requests if rate else 0)
    return (policy,delay), {'status':'available','attempts':attempts}


def download_url(source):
    # The original host declares this exact HTTPS, same-path 301 destination.
    # Keep source identity/cache filenames unchanged; never follow arbitrary redirects.
    return source.replace('https://www.bilinovel.com/','https://www.bilinovel.net/',1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    args = parser.parse_args()
    from django.conf import settings
    if not settings.configured:
        settings.configure(COVER_CACHE_DIR=args.cache_dir)
    from booklist import cover_cache
    assert args.database.is_file() and not args.cache_dir.is_symlink()
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with cache_lock(args.cache_dir):
        state = json.loads(args.checkpoint.read_text()) if args.checkpoint.exists() else {'items': {}}
        scope = {'database':str(args.database.resolve()),'cache_dir':str(args.cache_dir.resolve())}
        assert not state.get('scope') or state['scope'] == scope
        state.update(scope=scope, status='running', started_at=datetime.now(timezone.utc).isoformat())
        urls = observed(args.database)
        state['observed'] = len(urls)
        def save():
            state['updated_at'] = datetime.now(timezone.utc).isoformat()
            state['counts'] = dict(Counter(v['status'] for v in state['items'].values()))
            state['processed'] = len(state['items'])
            state['remaining'] = max(0,len(urls)-state['processed'])
            pending=args.checkpoint.with_suffix('.pending')
            pending.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            pending.replace(args.checkpoint)
        save()
        # Respect the operator's normal system network/proxy, as the browser does.
        opener = build_opener(transport.NoRedirect())
        transport.AGENT = 'YoitsuLibrary-CoverCache/1.0'
        policies = {}
        for original in urls:
            if (args.checkpoint.parent/'STOP').exists():
                state['status']='paused';save();return 0
            url=cover_cache.source_url(original)
            previous=state['items'].get(original)
            if url and cover_cache.cache_info(cover_cache.filename(url)):
                state['items'][original]={'status':'cached'}
                if len(state['items'])%20==0:save()
                continue
            if previous and previous['status'] in ('failed','restricted','unsupported') and previous.get('reason')!='robots_not_available_or_disallowed':
                continue
            if not url:
                state['items'][original]={'status':'unsupported'};save();continue
            target=download_url(url)
            origin='https://'+urlsplit(target).netloc
            if origin not in policies:
                policies[origin],diagnostic=policy_for(origin,opener)
                state.setdefault('policies',{})[origin]=diagnostic;save()
            rules=policies[origin]
            if not rules:
                state['items'][original]={'status':'policy_unavailable','reason':'robots_unavailable'};save();continue
            if rules[1]>60 or not rules[0].can_fetch(transport.AGENT,target):
                state['items'][original]={'status':'restricted','reason':'robots_disallowed_or_delay_requires_review'};save();continue
            state['current_url']=url;save()
            time.sleep(rules[1])
            record, raw=transport.fetch(target,cover_cache.MAX_BYTES,opener)
            item={'status':'failed','request':record}
            if record.get('status') in (401,403,429):
                item['status']='restricted'
            elif raw is not None:
                try:
                    info=cover_cache.store(url,raw,record['media_type'])
                    item={'status':'downloaded','name':cover_cache.filename(url),'bytes':info['bytes'],'retrieved_url':target}
                except ValueError as error:
                    item['reason']=str(error)
            state['items'][original]=item;save()
            print(json.dumps({'status':item['status'],'processed':state['processed'],'remaining':state['remaining']}),flush=True)
        state['status']='completed_with_errors' if any(k in state['counts'] for k in ('failed','restricted','unsupported','policy_unavailable')) else 'completed'
        state.pop('current_url',None);save()
        print(json.dumps({'status':state['status'],'counts':state['counts']}),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
