"""Book identification discussions and version-checked acceptance."""
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect

from . import catalog, services
from .catalog_views import page
from .course_common import bump, clean, conflict, course_page, deny, integer, require_reader, require_version, visible_work
from .course_models import BookCandidate, BookClue, BookQuestion


def questions():
    visible = catalog.visible_works().values('pk')
    return BookQuestion.objects.filter(hidden=False).exclude(
        Q(accepted__hidden=True) | (Q(accepted__isnull=False) & ~Q(accepted__work_id__in=visible)))


def question_url(question):
    return f'/book-help/{question.pk}/'


@course_page
def index(request):
    if request.method == 'POST':
        reader = require_reader(request)
        if BookQuestion.objects.filter(author=reader).count()>=30:
            conflict('每位读者最多发布 30 条求助。')
        question = BookQuestion.objects.create(author=reader, title=clean(request.POST.get('title', ''), 100),
                                              clues=clean(request.POST.get('clues', ''), 1000))
        return redirect(question_url(question))
    term = clean(request.GET.get('q', ''), 100, required=False)
    mode = request.GET.get('status', 'all')
    if mode not in ('all', 'open', 'solved'):
        raise ValidationError('请选择有效的求助状态。')
    results = BookQuestion.objects.filter(hidden=False).select_related('author', 'accepted__work')
    solved = questions().filter(accepted__isnull=False).values('pk')
    if mode == 'solved':
        results = results.filter(pk__in=solved)
    elif mode == 'open':
        results = results.exclude(pk__in=solved)
    if term:
        results = results.filter(Q(title__icontains=term) | Q(clues__icontains=term) | Q(additions__text__icontains=term)).distinct()
    from django.db.models import Case, When, Value, BooleanField
    results = results.annotate(solved=Case(When(pk__in=solved, then=Value(True)), default=Value(False), output_field=BooleanField()))
    hidden = BookQuestion.objects.filter(hidden=True).order_by('-id')[:50] if request.admin else []
    return page(request, 'booklist/course/help.html', {'questions': results.order_by('-updated_at', '-id')[:50],
        'q': term, 'mode': mode, 'hidden_questions': hidden})


@course_page
def detail(request, question_id):
    question = get_object_or_404(BookQuestion.objects.select_related('author'), pk=question_id)
    if question.hidden and not request.admin:
        from django.http import Http404
        raise Http404
    candidates = question.candidates.select_related('author', 'work').order_by('id')
    visible_candidates = candidates.filter(hidden=False, work_id__in=catalog.visible_works().values('pk'))
    accepted = visible_candidates.filter(pk=question.accepted_id).first() if question.accepted_id else None
    owner = request.account is not None and question.author_id == request.account.pk
    if request.method == 'POST':
        change(request, question, owner, accepted, visible_candidates)
        return redirect(question_url(question))
    return page(request, 'booklist/course/help_detail.html', {'question': question, 'candidates': visible_candidates,
        'hidden_candidates': candidates.filter(hidden=True, work_id__in=catalog.visible_works().values('pk')) if request.admin else [],
        'accepted': accepted, 'is_owner': owner, 'additions': question.additions.order_by('id')})


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
