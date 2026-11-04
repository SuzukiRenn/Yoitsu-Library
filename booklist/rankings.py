"""Curated annual rankings, resolved through stable catalog identities."""
from functools import lru_cache
import json
import re
from pathlib import Path
from django.conf import settings
from django.db.models import Q
from django.http import JsonResponse
from .models import Work, WorkAlias
from .catalog_views import page, parameters, read_limit
from .views import endpoint

@lru_cache(maxsize=1)
def dataset():
    data=json.loads((settings.BASE_DIR / 'static/rankings/data.json').read_text(encoding='utf-8'))
    assert data['format']==1 and sum(len(s['entries']) for y in data['years'] for s in y['sections'])==data['recordCount']
    return data

def curated_cover(value):
    prefix='/static/rankings/covers/'
    if not isinstance(value,str) or not value.startswith(prefix):return None
    name=value[len(prefix):]
    return value if re.fullmatch(r'[a-f0-9]{64}\.(jpg|png)',name) and name in dataset()['coverHashes'] else None

def catalog_bindings(data=None):
    data=data or dataset()
    definitions=data['works']
    wids={w['refs']['wenku8_id'] for w in definitions.values()}-{None}
    bids={w['refs']['bili_id'] for w in definitions.values()}-{None}
    unbound={key:row for key,row in definitions.items() if not any(row['refs'].values())}
    names={name for row in unbound.values() for name in row['aliases']}
    alias_rows=list(WorkAlias.objects.filter(alias__in=names,archived_at__isnull=True).values_list('work_id','alias'))
    query=Q(source_metadata__source_provider='konorano_rankings')
    if names:query|=Q(original_submitted_title__in=names)|Q(wenku8_title__in=names)|Q(title_override__in=names)|Q(id__in=[r[0] for r in alias_rows])
    if wids:query|=Q(wenku8_book_id__in=wids)
    if bids:query|=Q(source_metadata__bili_id__in=bids)
    records=list(Work.objects.filter(query).only('id','wenku8_book_id','source_metadata','hidden_at','review_status','merged_into_id','original_submitted_title','wenku8_title','title_override','title_overridden','wenku8_author','author_override','author_overridden'))
    wenku={w.wenku8_book_id:w for w in records if w.wenku8_book_id}
    bili={};ranked={}
    for w in records:
        if w.source_metadata.get('bili_id') is not None:bili.setdefault(w.source_metadata['bili_id'],[]).append(w)
        if w.source_metadata.get('ranking_key'):ranked.setdefault(w.source_metadata['ranking_key'],[]).append(w)
    aliases_by_id={}
    for work_id,alias in alias_rows:aliases_by_id.setdefault(work_id,set()).add(alias)
    from .catalog import search_key
    result={}
    for key,definition in definitions.items():
        refs=definition['refs'];matches={}
        candidate=wenku.get(refs['wenku8_id'])
        if candidate:matches[candidate.pk]=candidate
        for w in bili.get(refs['bili_id'],[]) + ranked.get(key,[]):matches[w.pk]=w
        if key in unbound:
            for w in records:
                if w.source_metadata.get('edition')=='web':continue
                titles={w.original_submitted_title,w.wenku8_title,w.display_title}|aliases_by_id.get(w.pk,set())
                author=w.author_override if w.author_overridden else w.wenku8_author or ''
                if titles.intersection(definition['aliases']) and author and search_key(author)==search_key(definition['author']):matches[w.pk]=w
        if len(matches)>1:raise ValueError('Ambiguous stable ranking reference: '+key)
        if matches:result[key]=next(iter(matches.values()))
    return result

def visible_binding(work):
    seen=set()
    while work and work.merged_into_id:
        if work.pk in seen or len(seen)>=10:return None
        seen.add(work.pk)
        work=Work.objects.filter(pk=work.merged_into_id).first()
    return work if work and work.hidden_at is None and work.review_status=='clear' and not work.source_metadata.get('manual_retired_at') else None

@endpoint('GET')
def home(request,data):
    parameters(request,('year','category','shown'))
    read_limit(request)
    return page(request,'booklist/rankings.html',{'navigation_rankings':True})

@endpoint('GET')
def listing(request,data):
    parameters(request,())
    read_limit(request)
    source=dataset()
    bound={key:visible_binding(w) for key,w in catalog_bindings().items()}
    # Never expose source identities, private metadata, or local filesystem paths.
    years=[{**y,'sections':[{**s,'entries':[{**e,'workUrl':f'/works/{bound[e["workKey"]].pk}/' if bound.get(e['workKey']) else None} for e in s['entries']]} for s in y['sections']]} for y in source['years']]
    response=JsonResponse({'years':years,'recordCount':source['recordCount']},json_dumps_params={'ensure_ascii':False})
    response['Cache-Control']='private, no-store'
    return response
