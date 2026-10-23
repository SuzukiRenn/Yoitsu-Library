"""Public Bili metadata parser and candidate comparison; no I/O at import."""
import ctypes
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from lxml import html

BOOK_CATEGORIES={'Fami通文库','GA文库','HJ文库','MF文库J','其他文库','富士见文库','少女文库','电击文库','角川文库'}

def stamp():return datetime.now(timezone.utc).isoformat()

def simplified(value):
    value=unicodedata.normalize('NFKC',value)
    api=ctypes.windll.kernel32.LCMapStringEx
    api.argtypes=[ctypes.c_wchar_p,ctypes.c_uint,ctypes.c_wchar_p,ctypes.c_int,ctypes.c_wchar_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ssize_t]
    api.restype=ctypes.c_int
    size=api('zh-CN',0x02000000,value,len(value),None,0,None,None,0)
    if not size:return value
    out=ctypes.create_unicode_buffer(size)
    if not api('zh-CN',0x02000000,value,len(value),out,size,None,None,0):raise RuntimeError('simplification_failed')
    return out[:size]

def key(value):
    return ''.join(c for c in simplified(value or '').casefold() if not c.isspace() and not unicodedata.category(c).startswith('P'))

def extract(raw,bid):
    t=html.fromstring(raw)
    titletext=' '.join(t.xpath('//title/text()')).lower()
    if any(s in titletext for s in ('captcha','access denied','人机验证','安全验证','just a moment')):raise ValueError('restriction_page')
    def meta(k):
        values=t.xpath('//meta[@property=$k]/@content',k=k)
        if len(values)!=1:raise ValueError('missing_or_ambiguous_'+k)
        return values[0].strip()
    names=t.xpath('//h1[@class="book-title"]/text()')
    if len(names)!=1:raise ValueError('ambiguous_title')
    title=names[0].strip();author=meta('og:novel:author')
    if not title or len(title)>300 or not author or len(author)>300:raise ValueError('invalid_identity')
    if title!=meta('og:title') or title!=meta('og:novel:book_name'):raise ValueError('identity_conflict')
    if meta('og:url')!=f'https://www.bilinovel.com/novel/{bid}.html':raise ValueError('source_id_mismatch')
    summaries=t.xpath('//*[@id="bookSummary"]/content')
    if len(summaries)!=1:raise ValueError('ambiguous_description')
    content=summaries[0]
    for br in content.xpath('.//br'):br.tail='\n'+(br.tail or '')
    description=content.text_content().strip()
    if not description or len(description)>10000:raise ValueError('invalid_description')
    groups=t.xpath('//span[contains(concat(" ",normalize-space(@class)," ")," tag-small-group ")]')
    if len(groups)!=1:raise ValueError('ambiguous_tags')
    tags=[n.text_content().strip() for n in groups[0].xpath('.//a')]
    category=meta('og:novel:category')
    web=any(key(n)=='web轻小说' for n in tags)
    book=category in BOOK_CATEGORIES and '日本轻小说' in tags
    edition='web' if web and not book else 'book' if book and not web else 'unknown'
    alias=[]
    for group in t.xpath('//aside[@class="backupname"]/span/text()'):
        alias.extend(n.strip() for n in re.split('[,，]',group) if n.strip())
    return {'bili_id':bid,'title':title,'author':author,'description':description,'aliases':list(dict.fromkeys(alias)),
        'edition':edition,'edition_label':{'web':'Web版','book':'文库版','unknown':'版本待核对'}[edition],
        'source_category':category,'tags':tags,'cover_url':meta('og:image'),'source_url':meta('og:url'),
        'source_updated_at':meta('og:novel:update_time'),'collected_at':stamp(),'html_sha256':hashlib.sha256(raw).hexdigest()}

def compare(item,rows):
    names={key(n) for n in [item['title']]+item['aliases']};author=key(item['author']);candidates=[]
    for row in rows:
        other={key(n) for n in row['names']};other.discard('')
        same_author=author in {key(n) for n in row['authors']}
        shared=bool(names & other);score=1.0 if shared else 0
        if not shared:
            for a in names:
                for b in other:
                    if not a or not b:continue
                    overlap=2*len(set(a)&set(b))/(len(set(a))+len(set(b)))
                    if min(len(a),len(b))/max(len(a),len(b))>=.45 and overlap>=.35:
                        score=max(score,SequenceMatcher(None,a,b,autojunk=False).ratio())
        if shared or same_author or score>=.65:
            candidates.append({'work_id':row['id'],'wenku8_id':row['wenku8_book_id'],'title':row['wenku8_title'] or row['original_submitted_title'],
                'edition':row['edition'],'name_match':shared,'author_match':same_author,'similarity':round(score,3),
                'restricted':bool(row['hidden_at'] or row['merged_into_id'] or row['review_status']!='clear')})
    same=[r for r in candidates if r['name_match'] and r['author_match'] and r['edition']==item['edition']!='unknown']
    related=[r for r in candidates if r['name_match'] and r['author_match'] and r['edition']!='unknown' and item['edition']!='unknown' and r['edition']!=item['edition']]
    if item['edition']=='unknown':decision='version_review'
    elif len(same)==1 and not same[0]['restricted']:decision='duplicate_candidate'
    elif same or any(r['restricted'] for r in candidates):decision='manual_review'
    elif related and len(related)==len(candidates):decision='new_version_candidate'
    elif candidates:decision='manual_review'
    else:decision='new_candidate'
    item.update(decision=decision,candidates=sorted(candidates,key=lambda r:(not r['name_match'],not r['author_match'],-r['similarity'])))
    return item

def detail_action(record,raw):
    status=record.get('status')
    if status in (401,403):return 'skip_restricted'
    if status==404:return 'skip_not_found'
    if status==429:return 'stop_rate_limited'
    if raw is None or status!=200:return 'stop_network_or_status'
    lower=raw.lower()
    if any(marker in lower for marker in (b'sorry, you have been blocked',b'cf-chl-',b'/cdn-cgi/challenge-platform/',b'<title>just a moment')):
        return 'skip_challenge'
    return 'parse'
