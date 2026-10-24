"""Display edition evidence without guessing from titles or missing source bindings."""
BOOK_CATEGORIES={'Fami通文库','GA文库','HJ文库','MF文库J','其他文库','富士见文库','少女文库','电击文库','角川文库'}
PUBLISHERS={'小学馆','集英社','一迅社','讲谈社'}


def edition_info(metadata):
    metadata=metadata if isinstance(metadata,dict) else {}
    explicit=metadata.get('edition')
    category=metadata.get('publisher_category')
    if not isinstance(category,str):category=''
    if explicit in ('web','book','other'):
        kind=explicit
    elif explicit is None and category in BOOK_CATEGORIES:
        kind='book'
    else:
        kind='unknown'
    labels={'web':'Web版','book':'文库版','other':'其他类型','unknown':'其他类型'}
    label=category if kind in ('unknown','book') and category in PUBLISHERS else labels[kind]
    return {'edition':kind,'edition_label':label}
