"""Search only the visible shelf, respecting displayed metadata and activity snapshots."""
from django.core.exceptions import ValidationError
from django.db.models import Case,When,F,Q,Value,TextField,OuterRef,Subquery
from django.db.models.functions import Coalesce,NullIf
from .models import UserWorkMetadata,WorkAlias
from .text import normalize

def search_text(value):
    value=normalize(value,'title')
    if len(value)>100:raise ValidationError('搜索内容最多100字')
    return value

def tag_ids(values):
    if len(values)>20 or any(not str(v).isascii() or not str(v).isdecimal() or str(int(v))!=str(v) or int(v)<=0 for v in values):
        raise ValidationError('标签无效')
    return sorted(set(map(int,values)))

def search_public(query,text):
    if not text:return query
    manual=Subquery(UserWorkMetadata.objects.filter(work_id=OuterRef('pk'),archived_at__isnull=True).values('author')[:1])
    source_author=Coalesce(F('wenku8_author'),Value(''),output_field=TextField())
    title=Case(When(title_overridden=True,then=F('title_override')),
        default=Coalesce(NullIf(F('wenku8_title'),Value('')),F('original_submitted_title'),Value('')),output_field=TextField())
    author=Case(When(author_overridden=True,then=F('author_override')),
        When(wenku8_book_id__isnull=True,then=Coalesce(NullIf(manual,Value('')),source_author,output_field=TextField())),
        default=source_author,output_field=TextField())
    aliases=WorkAlias.objects.filter(archived_at__isnull=True,alias__icontains=text).values('work_id')
    return query.alias(shelf_title=title,shelf_author=author).filter(Q(shelf_title__icontains=text)|Q(shelf_author__icontains=text)|Q(pk__in=aliases))
