"""User-selected in-site references; reason text stays plain and capped at 200 code points."""
from django.core.exceptions import ValidationError
from . import catalog


def validate(text,references,previous=None,work_id=None):
    old_text=getattr(previous,'reason','')
    old=getattr(previous,'reason_references',[]) or []
    if references is None:
        references=old if text==old_text else []
    if not isinstance(references,list) or len(references)>8:
        raise ValidationError('每条理由最多引用8部作品')
    targets={work.pk:work for work in catalog.visible_works().filter(pk__in=[r.get('work_id') for r in references if isinstance(r,dict) and type(r.get('work_id')) is int])}
    preserved={(r.get('work_id'),old_text[r.get('start',0):r.get('end',0)]) for r in old if isinstance(r,dict) and type(r.get('start')) is int and type(r.get('end')) is int}
    result=[];end=0
    for ref in references:
        if not isinstance(ref,dict) or set(ref)!={'work_id','start','end'} or any(type(ref[k]) is not int for k in ref):
            raise ValidationError('作品引用格式无效')
        wid,start,stop=ref['work_id'],ref['start'],ref['end']
        if wid<=0 or start<end or not 0<=start<stop<=len(text):
            raise ValidationError('作品引用位置无效或重叠')
        label=text[start:stop]
        keep=(wid,label) in preserved
        target=targets.get(wid)
        if not keep and (wid==work_id or target is None or target.display_title!=label):
            raise ValidationError('引用作品已变化，请重新搜索选择')
        result.append({'work_id':wid,'start':start,'end':stop});end=stop
    return result


def decorate(rows):
    rows=list(rows)
    ids={r.get('work_id') for row in rows for r in row.get('reason_references',[]) if isinstance(r,dict) and type(r.get('work_id')) is int}
    visible=set(catalog.visible_works().filter(pk__in=ids).values_list('pk',flat=True)) if ids else set()
    for row in rows:
        refs=row.pop('reason_references',[]) or []
        text=row['reason'];parts=[];position=0
        for ref in refs:
            start,stop=ref.get('start'),ref.get('end');wid=ref.get('work_id')
            if type(start) is not int or type(stop) is not int or not position<=start<stop<=len(text):continue
            if start>position:parts.append({'text':text[position:start]})
            part={'text':text[start:stop]}
            if wid in visible:part['url']=f'/works/{wid}/'
            parts.append(part);position=stop
        if position<len(text):parts.append({'text':text[position:]})
        row['parts']=parts
    return rows
