"""Staging deduplication policy. Human decisions remain separate and take priority."""
from scripts.bili_editions import import_edition
VERSION=4

def resolve(result,item,rows):
    byid={r['id']:r for r in rows}
    candidates=[c for c in result['candidates'] if c['name_match'] or c['similarity']>=.65]
    exact=[c for c in candidates if c['name_match'] and c['author_match']
           and item['edition']!='unknown' and c['edition']==item['edition']]
    if item['edition']=='other':
        exact=[c for c in exact if byid[c['work_id']].get('publisher_category')==item.get('source_category')]
    if len(exact)==1 and not exact[0]['restricted']:
        decision='auto_duplicate';target=exact[0]['work_id']
    else:
        target=None
        decision='new_candidate' if not candidates else 'version_review' if item['edition']=='unknown' else 'manual_review'
    return {'policy_version':VERSION,'decision':decision,'candidates':candidates,'edition_needs_review':item['edition']=='unknown','import_edition':import_edition(item['edition']),
            'auto_target_work_id':target,'disposition':'exclude_duplicate' if target else 'pending_review' if decision in ('manual_review','version_review') else 'new_candidate'}
