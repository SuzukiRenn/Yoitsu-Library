"""Replace identity shelf participation with unattributed content, never source actors or votes."""
import hashlib,json,secrets
from datetime import timedelta, timezone as datetime_timezone
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from . import catalog,services
from .models import (Work,WorkAlias,Tag,WorkTag,Activity,ActivityWork,AnonymousClient,ImportedPrincipal,
    Recommendation,PublicRecommendation,PublicLike,Vote,ReasonLike,Notification,WorkLikeEvent,
    PendingActivitySubmission,IdempotencyRecord,Admin,Account,AccountSession,AdminSession,EmailChallenge,ActivityNoticeSeen,
    Feedback,RateLimitBucket,UserWorkMetadata,SourceFieldState,TagSuppression,SourceIdentityReview,RecentWorkView)
from .user_metadata import tag_key

RESET=(Notification,WorkLikeEvent,ReasonLike,Recommendation,PublicLike,PublicRecommendation,Vote,PendingActivitySubmission)
PROTECTED=(Admin,Account,AccountSession,AdminSession,EmailChallenge,ActivityNoticeSeen,Feedback,RateLimitBucket,UserWorkMetadata,SourceFieldState,TagSuppression,SourceIdentityReview,RecentWorkView)

def digest(rows):return hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
def fingerprints(models):
    result={}
    for model in models:
        hasher=hashlib.sha256()
        for row in model.objects.order_by('pk').values().iterator(chunk_size=100):
            hasher.update(json.dumps(row,ensure_ascii=False,sort_keys=True,default=str).encode());hasher.update(b'\n')
        result[model.__name__]=hasher.hexdigest()
    return result

def canonical(row):
    seen=set()
    while row and row.merged_into_id:
        if row.pk in seen:raise ValueError('Cyclic catalog mapping')
        seen.add(row.pk);row=row.merged_into
    return row

def match_work(source):
    f=source['fields'];meta=f['source_metadata'];rows=[]
    if f['wenku8_book_id'] is not None:
        rows=list(Work.objects.filter(wenku8_book_id=f['wenku8_book_id']))
    elif meta.get('source_provider')=='bilinovel' and type(meta.get('bili_id')) is int:
        rows=list(Work.objects.filter(source_metadata__source_provider='bilinovel',source_metadata__bili_id=meta['bili_id']))
    else:
        title=f['title_override'] if f['title_overridden'] else f['wenku8_title'] or f['original_submitted_title']
        author=f['author_override'] if f['author_overridden'] else f['wenku8_author'] or ''
        rows=[w for w in catalog.visible_works() if w.display_title==title and (w.author_override if w.author_overridden else w.wenku8_author or '')==author
              and w.source_metadata.get('edition')==meta.get('edition')]
    rows={w.pk:w for row in rows if (w:=canonical(row)) is not None}
    if len(rows)>1:raise ValueError('Ambiguous work mapping: '+str(source['source_id']))
    row=next(iter(rows.values()),None)
    if row and (row.hidden_at or row.review_status!='clear'):
        raise ValueError('Target work is moderated: '+str(row.pk))
    return row

def make_plan(doc):
    assert doc['format']==1 and doc['source']=='anonymous_public_content'
    assert len(doc['public'])<=10000 and len(doc['activities'])<=1000
    assert len({r['source_id'] for r in doc['works']})==len(doc['works'])
    source_current=[p for p in doc['activities'] if p['fields']['title']==doc['mapping']['source_current_title'] and p['fields']['status'] in ('active','paused')]
    targets=list(Activity.objects.filter(title=doc['mapping']['target_current_title'],status__in=('active','paused')))
    if len(source_current)!=1 or len(targets)!=1:raise ValueError('Current activity mapping must be unique')
    works={}
    for source in doc['works']:
        matched=match_work(source)
        works[str(source['source_id'])]=matched.pk if matched else None
    mapped=[pk for pk in works.values() if pk]
    if len(set(mapped))!=len(mapped):raise ValueError('Distinct source works map to one target; manual review required')
    periods={}
    for p in doc['activities']:
        if p is source_current[0]:target=targets[0]
        else:
            rows=list(Activity.objects.filter(title=p['fields']['title'],start_at=date(p['fields']['start_at']),scheduled_end_at=date(p['fields']['scheduled_end_at'])))
            if len(rows)>1:raise ValueError('Ambiguous historical theme mapping')
            target=rows[0] if rows else None
            if target is None and p['fields']['status'] not in ('ended','scheduled'):
                raise ValueError('Unexpected additional live activity requires review')
        periods[str(p['source_id'])]=target.pk if target else None
    if not Admin.objects.filter(is_active=True).exists():raise ValueError('No active identity administrator')
    plan={'work_map':works,'activity_map':periods,'clear_counts':{m.__name__:m.objects.count() for m in RESET},
        'target_digest':fingerprints((*RESET,Activity,ActivityWork,Work)),
        'public_works':len(doc['public']),'public_reasons':sum(len(e['reasons']) for e in doc['public']),
        'themes':[{'source_id':p['source_id'],'title':p['fields']['title'],'works':len(p['entries']),
                   'reasons':sum(len(e['reasons']) for e in p['entries']),
                   'recommendations':sum(max(1,len(e['reasons'])) for e in p['entries'])} for p in doc['activities']]}
    return plan

def date(value):
    if value is None:return None
    parsed=parse_datetime(value)
    if parsed is None:raise ValueError('Invalid source date')
    return timezone.make_aware(parsed,datetime_timezone.utc) if timezone.is_naive(parsed) else parsed

def tag(name):
    rows=list(Tag.objects.filter(normalized_name=tag_key(name),merged_into__isnull=True))
    active=[t for t in rows if t.is_active]
    if len(active)>1:raise ValueError('Ambiguous target tag: '+name)
    if active:return active[0]
    if rows:raise ValueError('Target tag disabled: '+name)
    return Tag.objects.create(name=name,normalized_name=tag_key(name))

@transaction.atomic
def replace(doc,batch_key,expected_plan=None,preview=False):
    if ImportedPrincipal.objects.filter(batch_key=batch_key).exists():
        raise ValueError('Batch already applied; do not clear again')
    plan=make_plan(doc)
    if expected_plan and digest(plan)!=expected_plan:raise ValueError('Target changed since preview')
    protected=fingerprints(PROTECTED)
    now=timezone.now()
    for model in RESET:model.objects.all().delete()
    # Prevent stale successful retries from resurrecting old interaction responses.
    IdempotencyRecord.objects.filter(Q(scope__startswith='public.')|Q(scope__startswith='activity.')|Q(scope__startswith='reason.like.')).delete()
    ActivityWork.objects.filter(archived_at__isnull=True).update(archived_at=now,updated_at=now)
    principal_cache={}
    def principal(key):
        if key not in principal_cache:
            row=AnonymousClient.objects.create(token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),last_seen_at=now,expires_at=now+timedelta(days=36500))
            ImportedPrincipal.objects.create(principal=row,batch_key=batch_key);principal_cache[key]=row
        return principal_cache[key]
    works={}
    for source in doc['works']:
        pk=plan['work_map'][str(source['source_id'])]
        work=Work.objects.get(pk=pk) if pk else Work.objects.create(**source['fields'])
        works[source['source_id']]=work
        if not pk:
            for name in source['tags']:WorkTag.objects.get_or_create(work=work,tag=tag(name),source_type='source',state='active')
            for alias in source['aliases']:WorkAlias.objects.get_or_create(work=work,alias=alias,defaults={'normalized_alias':tag_key(alias)})
    def reasons(entry,activity=None,snap=None):
        for index,r in enumerate(entry['reasons']):
            assert isinstance(r['text'],str) and 0<len(r['text'])<=200
            refs=[{**ref,'work_id':works[ref['work_id']].pk} for ref in r['references'] if ref['work_id'] in works]
            Recommendation.objects.create(work=works[entry['work_id']],anonymous_client=principal(('reason',entry['work_id'],activity.pk if activity else None,index)),
                scope='activity' if activity else 'public',activity=activity,activity_work=snap,reason=r['text'],contains_spoilers=r['spoilers'],reason_references=refs)
    for entry in doc['public']:
        work=works[entry['work_id']]
        work.public_added_at=now;work.save(update_fields=['public_added_at','updated_at'])
        PublicRecommendation.objects.create(work=work,anonymous_client=principal(('public',entry['work_id'])),origin='legacy')
        reasons(entry)
    admin=Admin.objects.filter(is_active=True).order_by('pk').first()
    activity_map={}
    for source in doc['activities']:
        target_id=plan['activity_map'][str(source['source_id'])]
        if target_id:
            period=Activity.objects.get(pk=target_id)
            if source['fields']['title']==doc['mapping']['source_current_title']:
                period.title=doc['mapping']['renamed_current_title'];period.save(update_fields=['title','updated_at'])
        else:
            fields={key:date(value) if key.endswith('_at') or key=='scheduled_end_at' else value for key,value in source['fields'].items()}
            period=Activity.objects.create(**fields,created_by=admin)
        activity_map[str(source['source_id'])]=period.pk
        for entry in source['entries']:
            work=works[entry['work_id']]
            if work.public_added_at is None:
                work.public_added_at=now;work.save(update_fields=['public_added_at','updated_at'])
            snap=ActivityWork.objects.create(activity=period,work=works[entry['work_id']],**entry['snapshot'],
                snapshot_tags=[{'id':t.pk,'name':t.name} for name in entry['tags'] if (t:=tag(name))])
            # Existing theme semantics: each reason counts as a recommendation; empty entries need one seed.
            if not entry['reasons']:
                Recommendation.objects.create(scope='activity',activity=period,activity_work=snap,work=works[entry['work_id']],
                    anonymous_client=principal(('activity',source['source_id'],entry['work_id'])))
            reasons(entry,period,snap)
    assert fingerprints(PROTECTED)==protected,'Protected accounts or settings changed'
    assert not PublicLike.objects.exists() and not Vote.objects.exists() and not ReasonLike.objects.exists() and not Notification.objects.exists()
    assert PublicRecommendation.objects.count()==len(doc['public'])
    assert not Account.objects.filter(principal_id__in=ImportedPrincipal.objects.values('principal_id')).exists()
    from . import activities,public_booklist as books
    assert set(books.public_works().values_list('pk',flat=True))=={works[e['work_id']].pk for e in doc['public']}
    for entry in doc['public']:
        assert books.recommendations().filter(work=works[entry['work_id']]).count()==1
        assert books.reasons().filter(work=works[entry['work_id']]).count()==len(entry['reasons'])
    for source in doc['activities']:
        aid=activity_map[str(source['source_id'])]
        assert activities.recs(aid).count()==sum(max(1,len(e['reasons'])) for e in source['entries'])
        for entry in source['entries']:
            assert activities.recs(aid).filter(work=works[entry['work_id']]).count()==max(1,len(entry['reasons']))
            assert activities.recs(aid).filter(work=works[entry['work_id']]).exclude(reason='').count()==len(entry['reasons'])

    services.audit(None,'identity.shelf.seed',Work.objects.order_by('pk').first(),before=plan['clear_counts'],
        after={'batch_key':batch_key,'public_works':len(doc['public']),'activity_map':activity_map},reason='User-authorized anonymous content seed; no source identities or votes copied')
    result={'success':True,'public_works':len(doc['public']),'public_reasons':plan['public_reasons'],'activities':plan['themes'],
        'activity_map':activity_map,'accounts_and_sessions_preserved':True,'likes':0,'reason_likes':0,'notifications':0,'preview':preview,'plan_sha256':digest(plan)}
    if preview:transaction.set_rollback(True)
    return result
