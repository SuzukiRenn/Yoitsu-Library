import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Admin, AdminSession, AnonymousClient, AdminAuditLog, IdempotencyRecord, RateLimitBucket
from .text import normalize


class DomainError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def valid_token(token):
    return isinstance(token, str) and len(token) == 43 and all(c.isascii() and (c.isalnum() or c in '-_') for c in token)


def audit(admin, action, target, before=None, after=None, reason=''):
    # Only explicitly selected non-secret values may reach this function.
    return AdminAuditLog.objects.create(admin=admin, action=action, target_type=target._meta.label_lower,
        target_id=target.pk, before_data=before or {}, after_data=after or {}, reason=reason)


@transaction.atomic
def initialize_admin(username, password):
    if Admin.objects.exists():
        return None
    return _create_admin(None, username, password)


def _create_admin(actor, username, password):
    username = normalize(username, 'activity_title', required=True)
    validate_password(password)
    if Admin.objects.filter(username=username).exists():
        raise DomainError('username_exists', '管理员名称已存在', 409)
    admin = Admin.objects.create(username=username, password_hash=make_password(password))
    audit(actor, 'admin.create', admin, after={'username': username, 'is_active': True})
    return admin


def require_active(actor):
    if actor is None or not Admin.objects.filter(pk=actor.pk, is_active=True, session_version=actor.session_version).exists():
        raise DomainError('authentication_required', '需要有效管理员会话', 401)


@transaction.atomic
def create_admin(actor, username, password):
    require_active(actor)
    return _create_admin(actor, username, password)


@transaction.atomic
def disable_admin(actor, target_id):
    require_active(actor)
    try:
        target = Admin.objects.get(pk=target_id)
    except Admin.DoesNotExist:
        raise DomainError('not_found', '管理员不存在', 404)
    if not target.is_active:
        return
    if Admin.objects.filter(is_active=True).count() <= 1:
        raise DomainError('last_admin', '不能停用最后一名有效管理员', 409)
    target.is_active = False
    target.session_version += 1
    target.save(update_fields=['is_active', 'session_version', 'updated_at'])
    AdminSession.objects.filter(admin=target).delete()
    audit(actor, 'admin.disable', target, before={'is_active': True}, after={'is_active': False})


@transaction.atomic
def change_password(actor, current_password, new_password):
    require_active(actor)
    current = Admin.objects.get(pk=actor.pk)
    if not check_password(current_password, current.password_hash):
        raise DomainError('invalid_credentials', '当前密码错误', 403)
    validate_password(new_password)
    current.password_hash = make_password(new_password)
    current.password_changed_at = timezone.now()
    current.session_version += 1
    current.save(update_fields=['password_hash', 'password_changed_at', 'session_version', 'updated_at'])
    AdminSession.objects.filter(admin=current).delete()
    audit(current, 'admin.password_changed', current)


@transaction.atomic
def reset_admin_password(username, new_password):
    """Server-operator recovery only; deliberately has no HTTP route."""
    try:
        current = Admin.objects.get(username=username)
    except Admin.DoesNotExist:
        raise DomainError('not_found', '管理员不存在', 404)
    validate_password(new_password)
    current.password_hash = make_password(new_password)
    current.password_changed_at = timezone.now()
    current.session_version += 1
    current.save(update_fields=['password_hash', 'password_changed_at', 'session_version', 'updated_at'])
    AdminSession.objects.filter(admin=current).delete()
    audit(None, 'admin.password_reset', current, reason='server_operator_recovery')


def login(username, password):
    # Hash check outside SQLite write transaction, then recheck the exact credential version inside.
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 100 or len(password) > 4096:
        raise DomainError('invalid_credentials', '用户名或密码错误', 401)
    candidate = Admin.objects.filter(username=username).first()
    if candidate is None:
        make_password(password)
        raise DomainError('invalid_credentials', '用户名或密码错误', 401)
    if not check_password(password, candidate.password_hash) or not candidate.is_active:
        raise DomainError('invalid_credentials', '用户名或密码错误', 401)
    with transaction.atomic():
        current = Admin.objects.get(pk=candidate.pk)
        if not current.is_active or current.password_hash != candidate.password_hash or current.session_version != candidate.session_version:
            raise DomainError('invalid_credentials', '用户名或密码错误', 401)
        token = secrets.token_urlsafe(32)
        session = AdminSession.objects.create(admin=current, token_hash=token_hash(token),
            session_version=current.session_version, expires_at=timezone.now() + timedelta(seconds=settings.ADMIN_AGE))
        audit(current, 'admin.login', current)
        return token, session


@transaction.atomic
def issue_anonymous(existing=None, reset=False):
    now = timezone.now()
    if existing:
        current = AnonymousClient.objects.filter(pk=existing.pk, revoked_at__isnull=True, expires_at__gt=now).first()
        if current and not reset:
            current.expires_at = now + timedelta(seconds=settings.ANONYMOUS_AGE)
            current.last_seen_at = now
            current.save(update_fields=['expires_at', 'last_seen_at', 'updated_at'])
            return None, current
        if current:
            current.revoked_at = now
            current.save(update_fields=['revoked_at', 'updated_at'])
    token = secrets.token_urlsafe(32)
    client = AnonymousClient.objects.create(token_hash=token_hash(token), last_seen_at=now,
        expires_at=now + timedelta(seconds=settings.ANONYMOUS_AGE))
    return token, client


def rate_limit(scope, identifier, limit, seconds):
    slot = int(time.time()) // seconds
    key = hmac.new(settings.SECRET_KEY.encode(), f'{scope}:{identifier}:{slot}'.encode(), hashlib.sha256).hexdigest()
    with transaction.atomic():
        bucket, _ = RateLimitBucket.objects.get_or_create(key=key, defaults={'expires_at': timezone.now() + timedelta(seconds=seconds * 2)})
        if bucket.count >= limit:
            raise DomainError('rate_limited', '请求过于频繁，请稍后重试', 429)
        bucket.count += 1
        bucket.save(update_fields=['count'])


@transaction.atomic
def idempotent(client, scope, key, payload, operation):
    now = timezone.now()
    if not isinstance(key, str) or not 16 <= len(key) <= 200:
        raise DomainError('invalid_idempotency_key', '幂等键长度须为 16—200')
    if not AnonymousClient.objects.filter(pk=client.pk, revoked_at__isnull=True, expires_at__gt=now).exists():
        raise DomainError('identity_required', '匿名身份已失效', 401)
    digest = token_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
    record = IdempotencyRecord.objects.filter(anonymous_client=client, scope=scope, key_hash=token_hash(key)).first()
    if record and record.expires_at <= now:
        record.delete()
        record = None
    if record:
        if not hmac.compare_digest(record.request_hash, digest):
            raise DomainError('idempotency_conflict', '同一幂等键对应不同内容', 409)
        return record.response
    response = operation()
    IdempotencyRecord.objects.create(anonymous_client=client, scope=scope, key_hash=token_hash(key),
        request_hash=digest, response=response, expires_at=now + timedelta(seconds=settings.IDEMPOTENCY_AGE))
    return response
