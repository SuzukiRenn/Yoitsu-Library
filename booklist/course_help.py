"""Book identification discussions and version-checked acceptance."""
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.db.models import BooleanField, Case, Count, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from . import catalog, course_reply_likes, services
from .catalog_views import page
from .course_common import bump, clean, conflict, course_page, deny, integer, require_reader, require_version, visible_work
from .course_models import BookCandidate, BookClue, BookQuestion
from .course_reply_likes import visible_replies
from .navigation import safe_return


STATES = {'all': '全部', 'open': '求助中', 'solved': '已解决'}


def question_url(question):
    return f'/book-help/{question.pk}/'


@course_page
def index(request):
    if request.method == 'POST':
        reader = require_reader(request)
        if BookQuestion.objects.filter(author=reader, deleted_at__isnull=True).count()>=30:
            conflict('每位读者最多发布 30 条求助。')
        question = BookQuestion.objects.create(author=reader, title=clean(request.POST.get('title', ''), 100),
                                              clues=clean(request.POST.get('clues', ''), 1000))
        return redirect(question_url(question))
    term = clean(request.GET.get('q', ''), 100, required=False)
    mode = request.GET.get('status', 'all')
    if mode not in STATES:
        raise ValidationError('请选择有效的求助状态。')
    live = BookQuestion.objects.filter(deleted_at__isnull=True)
    results = live.filter(hidden=False).select_related('author')
    replies = visible_replies(BookCandidate.objects.all()).values('pk')
    solved = Q(accepted_id__in=replies)
    if mode == 'solved':
        results = results.filter(solved)
    elif mode == 'open':
        results = results.exclude(solved)
    if term:
        results = results.filter(Q(title__icontains=term) | Q(clues__icontains=term) | Q(additions__text__icontains=term))
    results = results.annotate(
        solved=Case(When(solved, then=Value(True)), default=Value(False), output_field=BooleanField()),
        reply_count=Count('candidates', filter=Q(candidates__in=replies), distinct=True),
    )
    query = {'q': term} if term else {}
    tabs = [{'key': key, 'label': label, 'url': '?' + urlencode({**query, 'status': key})}
            for key, label in STATES.items()]
    hidden = live.filter(hidden=True).order_by('-id')[:50] if request.admin else []
    return page(request, 'booklist/course/help.html', {'questions': results.order_by('-updated_at', '-id')[:50],
        'q': term, 'mode': mode, 'tabs': tabs, 'hidden_questions': hidden})


@course_page
def detail(request, question_id):
    question = get_object_or_404(BookQuestion.objects.select_related('author'), pk=question_id, deleted_at__isnull=True)
    if question.hidden and not request.admin:
        raise Http404
    owner = request.account is not None and question.author_id == request.account.pk
    source = safe_return(request.GET.get('from', ''), '')
    messages_source = source if source.split('?', 1)[0] == '/notifications/' else ''
    if request.method == 'POST' and request.POST.get('action') == 'delete_question':
        require_reader(request)
        if not owner or question.hidden:
            deny()
        require_version(request.POST, question)
        question.deleted_at = timezone.now()
        bump(question, 'deleted_at')
        course_reply_likes.retract_question_likes(question.pk)
        return redirect('/book-help/')

    candidates = question.candidates.select_related('author', 'work').order_by('id')
    visible_candidates = visible_replies(candidates)
    if request.method == 'POST' and request.POST.get('action') == 'like_reply':
        reader = require_reader(request)
        if question.hidden:
            deny()
        enabled = request.POST.get('enabled')
        if enabled not in ('0', '1'):
            raise ValidationError('请选择点赞或取消点赞。')
        item = get_object_or_404(visible_candidates, pk=integer(request.POST.get('candidate', '')))
        if item.author_id == reader.pk:
            deny()
        course_reply_likes.set_like(item, reader, enabled == '1')
        suffix = '?' + urlencode({'from': messages_source}) if messages_source else ''
        return redirect(f'{question_url(question)}{suffix}#reply-{item.pk}')

    accepted = visible_candidates.filter(pk=question.accepted_id).first() if question.accepted_id else None
    if request.method == 'POST':
        change(request, question, owner, accepted, visible_candidates)
        return redirect(question_url(question))
    return page(request, 'booklist/course/help_detail.html', {'question': question,
        'candidates': course_reply_likes.with_likes(visible_candidates, request.account),
        'hidden_candidates': candidates.filter(hidden=True, work_id__in=catalog.visible_works().values('pk')) if request.admin else [],
        'accepted': accepted, 'is_owner': owner, 'messages_source': messages_source,
        'additions': question.additions.order_by('id')})


def change(request, question, owner, accepted, visible_candidates):
    post = request.POST
    action = post.get('action')
    require_version(post, question)
    if action in ('hide_question', 'restore_question', 'hide_candidate', 'restore_candidate'):
        if request.admin is None:
            deny()
        if action.endswith('_question'):
            target, value = question, action == 'hide_question'
        else:
            target = get_object_or_404(question.candidates, pk=integer(post.get('candidate', '')))
            value = action == 'hide_candidate'
        before = target.hidden
        target.hidden = value
        target.save(update_fields=('hidden', 'updated_at'))
        bump(question)
        services.audit(request.admin, f'course.help.{action}', target, {'hidden': before}, {'hidden': value})
        return
    reader = require_reader(request)
    if question.hidden:
        deny()
    if action == 'add_clue' and owner:
        if question.additions.count()>=20:
            conflict('每条求助最多补充 20 次线索。')
        BookClue.objects.create(question=question, text=clean(post.get('clue', ''), 500))
    elif action == 'candidate':
        if owner:
            deny()
        if accepted:
            conflict('这条求助已有答案，撤销采纳后可继续提交候选。')
        if question.candidates.count()>=40:
            conflict('每条求助最多收录 40 部候选。')
        work = visible_work(integer(post.get('work', '')))
        if question.candidates.filter(work=work).exists():
            conflict('这部作品已被提交。')
        BookCandidate.objects.create(question=question, work=work, author=reader, reason=clean(post.get('reason', ''), 200))
    elif action in ('accept', 'unaccept') and owner:
        question.accepted = get_object_or_404(visible_candidates, pk=integer(post.get('candidate', ''))) if action == 'accept' else None
        bump(question, 'accepted')
        return
    else:
        deny()
    bump(question)
