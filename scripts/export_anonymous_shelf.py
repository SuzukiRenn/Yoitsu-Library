"""Export only public catalog content and visible reasons from a read-only SQLite snapshot."""
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import argparse,hashlib,json,sqlite3

FIELDS=('wenku8_book_id','original_submitted_title','wenku8_title','wenku8_author','wenku8_description',
 'wenku8_cover_url','wenku8_detail_url','title_override','title_overridden','author_override','author_overridden',
 'description_override','description_overridden','cover_url_override','cover_url_overridden','identity_status')
META=('source_provider','bili_id','edition','publisher_category','status','last_updated','word_count',
 'cover_cache_version','preserve_user_description','manual_retired_at')
ACTIVITY=('title','theme','start_at','scheduled_end_at','started_at','ended_at','cancelled_at','status','end_reason','cancel_reason','corrected_at')

def export(database):
    with closing(sqlite3.connect(Path(database).absolute().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        rows=lambda sql,args=():[dict(row) for row in db.execute(sql,args)]
        all_works={r['id']:r for r in rows('SELECT * FROM booklist_work')}
        for row in all_works.values():row['source_metadata']=json.loads(row['source_metadata'])
        visible=lambda w,historical=False:w and w['hidden_at'] is None and w['merged_into_id'] is None and w['review_status']=='clear' and (historical or 'manual_retired_at' not in w['source_metadata'])
        pub_ids={r['work_id'] for r in rows('SELECT DISTINCT work_id FROM booklist_publicrecommendation WHERE withdrawn_at IS NULL AND invalidated_at IS NULL AND archived_at IS NULL')}
        pub_ids={pk for pk in pub_ids if visible(all_works.get(pk)) and all_works[pk]['public_added_at'] is not None}
        reason=lambda row:{'text':row['reason'],'spoilers':bool(row['contains_spoilers']),
                           'references':json.loads(row['reason_references'])}
        all_reasons=rows("SELECT id,work_id,activity_id,activity_work_id,reason,contains_spoilers,reason_references FROM booklist_recommendation WHERE archived_at IS NULL AND invalidated_at IS NULL AND withdrawn_at IS NULL AND text_hidden_at IS NULL AND reason<>'' ORDER BY created_at,id")
        public=[{'work_id':pk,'reasons':[reason(r) for r in all_reasons if r['work_id']==pk and r['activity_id'] is None]} for pk in sorted(pub_ids)]
        periods=[]
        work_ids=set(pub_ids)
        for period in rows("SELECT * FROM booklist_activity WHERE status NOT IN ('draft','cancelled') ORDER BY start_at,id"):
            entries=[]
            sql='''SELECT aw.* FROM booklist_activitywork aw WHERE aw.activity_id=? AND aw.archived_at IS NULL AND aw.merged_into_id IS NULL
                AND EXISTS(SELECT 1 FROM booklist_recommendation r WHERE r.activity_work_id=aw.id AND r.scope='activity' AND r.archived_at IS NULL AND r.invalidated_at IS NULL AND r.withdrawn_at IS NULL) ORDER BY aw.id'''
            for snap in rows(sql,(period['id'],)):
                pk=snap['work_id']
                if not visible(all_works.get(pk),period['status']=='ended'):continue
                work_ids.add(pk)
                entries.append({'work_id':pk,'snapshot':{key:snap[key] for key in ('snapshot_title','snapshot_author','snapshot_cover_url','snapshot_description')},
                    'tags':[r['name'] for r in json.loads(snap['snapshot_tags'] or '[]')],
                    'reasons':[reason(r) for r in all_reasons if r['work_id']==pk and r['activity_id']==period['id'] and r['activity_work_id']==snap['id']]})
            periods.append({'source_id':period['id'],'fields':{key:period.get(key) for key in ACTIVITY},'entries':entries})
        for entry in public+[e for p in periods for e in p['entries']]:
            for r in entry['reasons']:
                r['references']=[ref for ref in r['references'] if isinstance(ref,dict) and visible(all_works.get(ref.get('work_id')))]
                work_ids.update(ref['work_id'] for ref in r['references'])
        works=[]
        for pk in sorted(work_ids):
            row=all_works[pk]
            fields={key:row.get(key) for key in FIELDS}
            fields['source_metadata']={key:row['source_metadata'][key] for key in META if key in row['source_metadata']}
            manual=rows('SELECT author,description FROM booklist_userworkmetadata WHERE work_id=? AND archived_at IS NULL',(pk,))
            if manual and row['wenku8_book_id'] is None:
                if not fields['author_overridden']:fields.update(author_override=manual[0]['author'],author_overridden=True)
                if not fields['description_overridden']:fields.update(description_override=manual[0]['description'],description_overridden=True)
            tags=rows('''SELECT DISTINCT t.name FROM booklist_worktag wt JOIN booklist_tag t ON t.id=wt.tag_id
                WHERE wt.work_id=? AND wt.state='active' AND t.is_active=1 AND t.merged_into_id IS NULL
                AND NOT EXISTS(SELECT 1 FROM booklist_tagsuppression s WHERE s.work_id=wt.work_id AND s.tag_id=t.id AND s.is_active=1 AND (s.scope='all' OR (s.scope='source' AND wt.source_type='source'))) ORDER BY t.name''',(pk,))
            works.append({'source_id':pk,'fields':fields,'tags':[r['name'] for r in tags],
                'aliases':[r['alias'] for r in rows('SELECT alias FROM booklist_workalias WHERE work_id=? AND archived_at IS NULL ORDER BY id',(pk,))]})
        return {'format':1,'source':'anonymous_public_content','captured_at':datetime.now(timezone.utc).isoformat(),
            'mapping':{'source_current_title':'最喜欢的三本','target_current_title':'十一月份推荐','renamed_current_title':'最喜欢的三本'},
            'works':works,'public':public,'activities':periods}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--database',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=export(args.database)
    payload=json.dumps(result,ensure_ascii=False,sort_keys=True).encode()
    path=Path(args.output);path.write_bytes(payload);path.chmod(0o600)
    print(json.dumps({'public_works':len(result['public']),'activities':[{'title':p['fields']['title'],'works':len(p['entries']),
        'reasons':sum(len(e['reasons']) for e in p['entries'])} for p in result['activities']],
        'public_reasons':sum(len(e['reasons']) for e in result['public']),'sha256':hashlib.sha256(payload).hexdigest()},ensure_ascii=False))
