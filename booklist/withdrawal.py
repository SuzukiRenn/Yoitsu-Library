"""Check the actual final recommendation within the idempotent write transaction."""
from django.core import signing
from . import services

SALT = 'booklist.withdrawal.v1'


def require_confirmation(work, client, activity_id=None, token=None):
    from django.db import connection
    if not connection.in_atomic_block:
        raise RuntimeError('Withdrawal confirmation must share the write transaction')
    from . import activities, public_booklist as books
    from .models import Recommendation
    from .lifecycle import manual_can_retire
    query = activities.recs(activity_id) if activity_id is not None else books.recommendations()
    query = query.filter(work=work)
    own = query.filter(anonymous_client=client).first()
    if own is None or query.exclude(pk=own.pk).exists():
        return
    # Mirror retirement rules while excluding only the relation being withdrawn.
    public = books.recommendations().filter(work=work)
    current = Recommendation.objects.filter(work=work, scope='activity', withdrawn_at__isnull=True,
        archived_at__isnull=True, invalidated_at__isnull=True, activity__status__in=['active', 'paused'],
        activity_work__archived_at__isnull=True, activity_work__merged_into__isnull=True)
    if activity_id is None:
        public = public.exclude(pk=own.pk)
    else:
        current = current.exclude(pk=own.pk)
    retires = manual_can_retire(work) and not public.exists() and not current.exists()
    expected = {'actor':client.pk, 'work':work.pk, 'activity':activity_id, 'relation':own.pk, 'retires':retires}
    if token:
        try:
            if signing.loads(token, salt=SALT, max_age=300) == expected:
                return
        except signing.BadSignature:
            pass
    scope = '本主题书单' if activity_id is not None else '公共书架'
    message = f'这是当前最后一份推荐。撤回后，这本书将退出{scope}，本轮理由与点赞将归档。'
    if activity_id is not None:
        message += '公共书架中的推荐不会撤回。'
    if retires:
        message += '这本手动添加的作品也将退出候选，再次推荐需重新填写资料。'
    error = services.DomainError('withdrawal_confirmation_required', message, 409)
    error.confirmation = {'token':signing.dumps(expected,salt=SALT), 'message':message}
    raise error
