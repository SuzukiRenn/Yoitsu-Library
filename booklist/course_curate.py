"""Role-scoped collaborative drafts and immutable public editions."""
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.db.models import Q

from . import catalog, services
from .catalog_views import page
from .course_common import bump, clean, conflict, course_page, deny, integer, require_reader, require_version, visible_work
from .course_models import CuratedShelf, Curator, CuratedEdition, Nomination
from .models import Account


def shelf_url(shelf):
    return f'/curated/{shelf.pk}/'


def role_for(shelf, account):
    if account is None:
        return ''
    if shelf.owner_id == account.pk:
        return 'owner'
    return shelf.memberships.filter(account=account).values_list('role', flat=True).first() or ''


@course_page
def index(request):
    if request.method == 'POST':
        owner = require_reader(request)
        title = clean(request.POST.get('title', ''), 100)
        if CuratedShelf.objects.filter(owner=owner).count() >= 20:
            conflict('每位读者最多创建 20 份策展。')
        return redirect(shelf_url(CuratedShelf.objects.create(title=title, owner=owner)))
    public = CuratedShelf.objects.filter(hidden=False, editions__isnull=False).distinct().order_by('-id')[:50]
    mine = CuratedShelf.objects.none()
    if request.account:
        mine = CuratedShelf.objects.filter(hidden=False).filter(Q(owner=request.account) | Q(memberships__account=request.account)).distinct().order_by('-id')[:50]
    moderation = CuratedShelf.objects.filter(hidden=True).order_by('-id')[:50] if request.admin else []
    return page(request, 'booklist/course/curated.html', {'public_shelves': public, 'my_shelves': mine, 'moderation_shelves': moderation})


def entry_diff(older, newer):
    old = {entry['work_id']: (position, entry) for position, entry in enumerate(older)}
    new = {entry['work_id']: (position, entry) for position, entry in enumerate(newer)}
    for work_id, (position, entry) in new.items():
        previous = old.get(work_id)
        if previous is None:
            yield work_id, '新增'
        elif previous != (position, entry):
            yield work_id, '调整'
    for work_id in old.keys() - new.keys():
        yield work_id, '移除'


def snapshot_rows(entries):
    visible = set(catalog.visible_works().filter(pk__in=[e['work_id'] for e in entries]).values_list('pk', flat=True))
    return [{**entry, 'visible': True} if entry['work_id'] in visible else
            {'work_id': entry['work_id'], 'title': '作品暂不可见', 'visible': False} for entry in entries]


@course_page
def detail(request, shelf_id):
    shelf = get_object_or_404(CuratedShelf, pk=shelf_id)
    role = role_for(shelf, request.account)
    can_review = role == 'owner'
    if request.method == 'POST':
        change(request, shelf, role)
        return redirect(shelf_url(shelf))

    editions = shelf.editions.order_by('-number')
    latest = editions.first()
    if (shelf.hidden or (latest is None and not role)) and not request.admin:
        raise services.DomainError('not_found', '书单不存在或暂不可见。', 404)
    wanted = request.GET.get('edition')
    edition = get_object_or_404(editions, number=integer(wanted)) if wanted is not None else latest
    snapshot = snapshot_rows(edition.entries) if edition else []
    changes = []
    if edition and edition.number > 1:
        before = editions.get(number=edition.number-1)
        titles = {row['work_id']: row['title'] for row in snapshot_rows(before.entries + edition.entries)}
        changes = [(titles[wid], label) for wid, label in entry_diff(before.entries, edition.entries)]
    nominations = []
    if role and not shelf.hidden:
        draft_items = list(shelf.nominations.select_related('work', 'author').order_by('position', 'pk'))
        visible = set(catalog.visible_works().filter(pk__in=[item.work_id for item in draft_items]).values_list('pk', flat=True))
        nominations = [{'item': item, 'visible': item.work_id in visible} for item in draft_items]
    return page(request, 'booklist/course/curated_detail.html', {'shelf': shelf, 'role': role, 'can_review': can_review, 'can_edit_draft': role in ('owner', 'editor'),
        'edition': edition, 'editions': editions[:30], 'snapshot': snapshot, 'changes': changes, 'nominations': nominations,
        'memberships': shelf.memberships.select_related('account').order_by('pk') if role == 'owner' else []})


def change(request, shelf, role):
    post = request.POST
    action = post.get('action')
    if action in ('hide', 'restore'):
        if not request.admin:
            deny()
        require_version(post, shelf)
        before = shelf.hidden
        shelf.hidden = action == 'hide'
        bump(shelf, 'hidden')
        services.audit(request.admin, f'course.curated.{action}', shelf, {'hidden': before}, {'hidden': shelf.hidden})
        return
    reader = require_reader(request)
    if shelf.hidden or not role:
        deny()
    require_version(post, shelf)
    if action == 'nominate':
        if shelf.nominations.count() >= 40:
            conflict('书单草稿最多收录 40 部作品。')
        work = visible_work(integer(post.get('work', '')))
        if shelf.nominations.filter(work=work).exists():
            conflict('这部作品已在草稿中。')
        reason = clean(post.get('reason', ''), 200)
        Nomination.objects.create(shelf=shelf, work=work, author=reader, reason=reason, position=40)
    elif action in ('invite', 'remove_member') and role == 'owner':
        if action == 'invite':
            member = Account.objects.filter(pk=integer(post.get('reader_id', '')), is_active=True).exclude(pk=reader.pk).first()
            new_role = post.get('role')
            if member is None or new_role not in ('member', 'editor'):
                raise ValidationError('请输入有效读者编号并选择角色。')
            if shelf.memberships.count() >= 20 and not shelf.memberships.filter(account=member).exists():
                conflict('最多邀请 20 位成员。')
            shelf.memberships.update_or_create(account=member, defaults={'role': new_role})
        else:
            get_object_or_404(shelf.memberships, pk=integer(post.get('member', ''))).delete()
    elif action in ('review', 'remove', 'edit_item'):
        item = get_object_or_404(shelf.nominations, pk=integer(post.get('item', '')))
        is_author = item.author_id == reader.pk
        can_review = role == 'owner'
        can_manage = role in ('owner', 'editor')
        if (action == 'review' and not can_review) or (action in ('edit_item', 'remove') and not (is_author or can_manage)):
            deny()
        if action == 'remove':
            item.delete()
        else:
            original_reason = item.reason
            if is_author and 'reason' in post:
                item.reason = clean(post['reason'], 200)
            original_position = item.position
            item.position = integer(post.get('position', ''), minimum=0, maximum=999)
            if can_review and action == 'review':
                item.approved = post.get('approved') == 'on'
            elif item.reason != original_reason or item.position != original_position:
                item.approved = False
            if item.approved:
                visible_work(item.work_id)
            item.save(update_fields=('position', 'approved', 'reason', 'updated_at'))
    elif action == 'publish' and role == 'owner':
        publish(shelf, reader)
    else:
        deny()
    bump(shelf)


def publish(shelf, reader):
    approved = list(shelf.nominations.filter(approved=True).select_related('work').order_by('position', 'id'))
    if not approved:
        conflict('请先审核通过至少一部作品。')
    visible = set(catalog.visible_works().filter(pk__in=[item.work_id for item in approved]).values_list('pk', flat=True))
    if len(visible) != len(approved):
        conflict('部分作品已不可见，请先调整草稿。')
    entries = [{'work_id': item.work_id, 'title': item.work.display_title, 'reason': item.reason} for item in approved]
    previous = shelf.editions.order_by('-number').first()
    if previous and previous.entries == entries:
        conflict('草稿与最近发布版本相同。')
    CuratedEdition.objects.create(shelf=shelf, number=previous.number+1 if previous else 1,
                                  entries=entries, published_by=reader)
