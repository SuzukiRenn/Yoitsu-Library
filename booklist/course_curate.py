"""Role-scoped collaborative drafts and immutable public editions."""
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.db.models import Prefetch, Q

from . import accounts, catalog, services
from .catalog_views import page
from .course_common import bump, clean, conflict, course_page, deny, integer, require_reader, require_version, visible_work
from .course_models import CuratedShelf, Curator, CuratedEdition, Nomination
from .models import Account

NOTICES = {
    'added': '作品已加入草稿。',
    'proposed': '提议已提交，等待创建者审核。',
    'accepted': '已接纳这部作品。',
    'returned': '作品已退回待审核。',
    'updated': '草稿已更新。',
    'removed': '作品已从草稿移除。',
    'published': '新版本已发布。',
    'invited': '协作者已添加或更新。',
    'member_removed': '协作者已移除。',
}

def shelf_url(shelf):
    return f'/curated/{shelf.pk}/'


def role_for(shelf, account):
    if account is None:
        return ''
    if shelf.owner_id == account.pk:
        return 'owner'
    return shelf.memberships.filter(account=account).values_list('role', flat=True).first() or ''


def public_shelf_cards(shelves):
    entries = [entry for shelf in shelves for entry in shelf.latest_editions[0].entries]
    works = catalog.visible_works().in_bulk({entry['work_id'] for entry in entries})
    cards = []
    for shelf in shelves:
        edition = shelf.latest_editions[0]
        visible = [entry for entry in edition.entries if entry['work_id'] in works]
        previews = [{'title': entry['title'], 'cover_url': catalog.display_cover(works[entry['work_id']])}
                    for entry in visible[:3]]
        cards.append({'shelf': shelf, 'edition': edition.number, 'count': len(visible), 'works': previews})
    return cards


@course_page
def index(request):
    if request.method == 'POST':
        owner = require_reader(request)
        title = clean(request.POST.get('title', ''), 100)
        if CuratedShelf.objects.filter(owner=owner).count() >= 20:
            conflict('每位读者最多创建 20 份策展。')
        return redirect(shelf_url(CuratedShelf.objects.create(title=title, owner=owner)))
    latest = Prefetch('editions', queryset=CuratedEdition.objects.order_by('-number')[:1], to_attr='latest_editions')
    public = CuratedShelf.objects.filter(hidden=False, editions__isnull=False).distinct().order_by('-id').prefetch_related(latest)[:50]
    mine = []
    if request.account:
        memberships = Prefetch('memberships',
            queryset=Curator.objects.filter(account=request.account), to_attr='my_memberships')
        shelves = CuratedShelf.objects.filter(hidden=False).filter(
            Q(owner=request.account) | Q(memberships__account=request.account)
        ).distinct().order_by('-updated_at', '-id').prefetch_related(latest, memberships)[:50]
        for shelf in shelves:
            role = '创建者' if shelf.owner_id == request.account.pk else shelf.my_memberships[0].get_role_display()
            mine.append({'shelf': shelf, 'role': role,
                         'edition': shelf.latest_editions[0].number if shelf.latest_editions else None})
    moderation = CuratedShelf.objects.filter(hidden=True).order_by('-id')[:50] if request.admin else []
    return page(request, 'booklist/course/curated.html', {'public_shelves': public_shelf_cards(list(public)), 'my_shelves': mine, 'moderation_shelves': moderation})


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


def edition_entries(items):
    return [{'work_id': item.work_id, 'title': item.work.display_title, 'reason': item.reason} for item in items]


def draft_context(shelf, latest):
    items = list(shelf.nominations.select_related('work', 'author').order_by('position', 'pk'))
    visible = set(catalog.visible_works().filter(pk__in=[item.work_id for item in items]).values_list('pk', flat=True))
    accepted = [item for item in items if item.approved and item.work_id in visible]
    waiting = [item for item in items if not item.approved and item.work_id in visible]
    blocked = any(item.approved and item.work_id not in visible for item in items)
    unchanged = bool(latest and latest.entries == edition_entries(accepted) and not blocked)
    return {
        'nominations': [{'item': item, 'visible': item.work_id in visible} for item in items],
        'order_options': range(1, len(items) + 1),
        'ready_count': len(accepted),
        'pending_count': len(waiting),
        'first_pending': waiting[0].pk if waiting else None,
        'publish_blocked': blocked,
        'unchanged': unchanged,
        'can_publish': bool(accepted) and not blocked and not unchanged,
    }


@course_page
def detail(request, shelf_id):
    shelf = get_object_or_404(CuratedShelf, pk=shelf_id)
    role = role_for(shelf, request.account)
    if request.method == 'POST':
        notice = change(request, shelf, role)
        return redirect(shelf_url(shelf) + ('?' + urlencode({'done': notice}) if notice else ''))

    editions = shelf.editions.order_by('-number')
    latest = editions.first()
    if (shelf.hidden or (latest is None and not role)) and not request.admin:
        raise services.DomainError('not_found', '书单不存在或暂不可见。', 404)
    wanted = request.GET.get('edition')
    has_draft = bool(role and not shelf.hidden)
    show_draft = has_draft and wanted is None
    edition = get_object_or_404(editions, number=integer(wanted)) if wanted is not None else (latest if not show_draft else None)
    snapshot = snapshot_rows(edition.entries) if edition else []
    changes = []
    if edition and edition.number > 1:
        before = editions.get(number=edition.number-1)
        titles = {row['work_id']: row['title'] for row in snapshot_rows(before.entries + edition.entries)}
        changes = [(titles[wid], label) for wid, label in entry_diff(before.entries, edition.entries)]
    notice = request.GET.get('done') if show_draft else None
    return page(request, 'booklist/course/curated_detail.html', {
        'shelf': shelf, 'role': role, 'can_review': role == 'owner', 'can_edit_draft': role in ('owner', 'editor'),
        'has_draft': has_draft, 'show_draft': show_draft, 'latest': latest, 'edition': edition,
        'editions': editions[:30] if edition else [], 'snapshot': snapshot, 'changes': changes,
        'notice': NOTICES.get(notice), 'offer_another': notice in ('added', 'proposed'),
        'memberships': shelf.memberships.select_related('account').order_by('pk') if show_draft and role == 'owner' else [],
        **(draft_context(shelf, latest) if show_draft else {}),
    })


def invited_reader(post, reader):
    readers = Account.objects.filter(is_active=True).exclude(pk=reader.pk)
    if nickname := post.get('display_name', '').strip():
        names = list(readers.filter(display_name=accounts.display_name(nickname))[:2])
        if len(names) > 1:
            conflict('有多位读者使用这个昵称，请按读者编号邀请。')
        return names[0] if names else None
    return readers.filter(pk=integer(post.get('reader_id', ''))).first()


def reorder(shelf, item, value):
    ordered = list(shelf.nominations.order_by('position', 'pk').values_list('pk', 'position'))
    target = integer(value, maximum=len(ordered)) - 1
    current = next(index for index, (pk, _) in enumerate(ordered) if pk == item.pk)
    if current == target:
        return False
    ordered.insert(target, ordered.pop(current))
    item.position = target
    Nomination.objects.bulk_update([
        Nomination(pk=pk, position=index)
        for index, (pk, position) in enumerate(ordered) if pk != item.pk and index != position
    ], ('position',))
    return True


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
    notice = 'updated'
    if action == 'nominate':
        if shelf.nominations.count() >= 40:
            conflict('书单草稿最多收录 40 部作品。')
        work = visible_work(integer(post.get('work', '')))
        if shelf.nominations.filter(work=work).exists():
            conflict('这部作品已在草稿中。')
        reason = clean(post.get('reason', ''), 200)
        last = shelf.nominations.order_by('-position').values_list('position', flat=True).first()
        Nomination.objects.create(shelf=shelf, work=work, author=reader, reason=reason,
                                  position=last + 1 if last is not None else 0, approved=role == 'owner')
        notice = 'added' if role == 'owner' else 'proposed'
    elif action in ('invite', 'remove_member') and role == 'owner':
        if action == 'invite':
            member = invited_reader(post, reader)
            new_role = post.get('role')
            if member is None or new_role not in ('member', 'editor'):
                raise ValidationError('未找到可邀请的读者，请核对昵称或读者编号。')
            if shelf.memberships.count() >= 20 and not shelf.memberships.filter(account=member).exists():
                conflict('最多邀请 20 位成员。')
            shelf.memberships.update_or_create(account=member, defaults={'role': new_role})
            notice = 'invited'
        else:
            get_object_or_404(shelf.memberships, pk=integer(post.get('member', ''))).delete()
            notice = 'member_removed'
    elif action in ('review', 'remove', 'edit_item'):
        item = get_object_or_404(shelf.nominations, pk=integer(post.get('item', '')))
        is_author = item.author_id == reader.pk
        can_review = role == 'owner'
        can_manage = role in ('owner', 'editor')
        if (action == 'review' and not can_review) or (action in ('edit_item', 'remove') and not (is_author or can_manage)):
            deny()
        if action == 'remove':
            item.delete()
            notice = 'removed'
        else:
            original_reason = item.reason
            if is_author and 'reason' in post:
                item.reason = clean(post['reason'], 200)
            if 'order' in post:
                reordered = reorder(shelf, item, post['order'])
            else:
                original_position = item.position
                if 'position' in post or action == 'edit_item':
                    item.position = integer(post.get('position', ''), minimum=0, maximum=999)
                reordered = item.position != original_position
            if can_review and action == 'review':
                item.approved = post.get('approved') == 'on'
                notice = 'accepted' if item.approved else 'returned'
            elif not can_review and (item.reason != original_reason or reordered):
                item.approved = False
            if item.approved:
                visible_work(item.work_id)
            item.save(update_fields=('position', 'approved', 'reason', 'updated_at'))
    elif action == 'publish' and role == 'owner':
        publish(shelf, reader)
        notice = 'published'
    else:
        deny()
    bump(shelf)
    return notice


def publish(shelf, reader):
    approved = list(shelf.nominations.filter(approved=True).select_related('work').order_by('position', 'id'))
    if not approved:
        conflict('请先审核通过至少一部作品。')
    visible = set(catalog.visible_works().filter(pk__in=[item.work_id for item in approved]).values_list('pk', flat=True))
    if len(visible) != len(approved):
        conflict('部分作品已不可见，请先调整草稿。')
    entries = edition_entries(approved)
    previous = shelf.editions.order_by('-number').first()
    if previous and previous.entries == entries:
        conflict('草稿与最近发布版本相同。')
    CuratedEdition.objects.create(shelf=shelf, number=previous.number+1 if previous else 1,
                                  entries=entries, published_by=reader)
