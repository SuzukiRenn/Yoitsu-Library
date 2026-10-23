"""Preserve explicit non-novel media without guessing unresolved editions."""
OTHER_CATEGORIES={'游戏剧本','广播剧','广播剧脚本','广播剧译文'}
PUBLISHERS={'小学馆','集英社','一迅社','讲谈社'}
LABELS={'web':'Web版','book':'文库版','other':'其他类型','unknown':'版本待核对'}

def display_label(edition,category):
    return category if edition=='book' and category in PUBLISHERS else LABELS[edition]

def effective_category(item):
    if item.get('source_category'):return item['source_category']
    supplement=item.get('publisher_supplement') or {}
    return supplement.get('value') if supplement.get('confirmed') is True and supplement.get('value') in PUBLISHERS else ''

def import_edition(edition):
    """Final import label; unknown evidence stays unknown during matching."""
    return edition if edition in ('web','book') else 'other'

def classify(edition,category,tags=None):
    source_tags=tags or ()
    if category in OTHER_CATEGORIES:
        # Conflicting explicit evidence still needs human review.
        return 'other' if edition in ('unknown','other') and 'Web轻小说' not in source_tags else 'unknown'
    if category in PUBLISHERS and edition=='unknown' and (tags is None or '日本轻小说' in source_tags) and 'Web轻小说' not in source_tags:
        return 'book'
    return edition

def apply(item):
    category=effective_category(item)
    item['edition']=classify(item.get('edition','unknown'),category,item.get('tags',()))
    item['edition_label']=display_label(item['edition'],category)
    item['publication_label']=display_label('book',category)
    return item
