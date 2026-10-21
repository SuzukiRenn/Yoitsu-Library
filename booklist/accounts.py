"""Local accounts. Account principals are never bound to pre-existing cookies."""
import re
import secrets
import unicodedata
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from .models import Account, AccountSession, AnonymousClient
from .services import DomainError, token_hash


def username(value):
    value = value.strip().lower()
    if not re.fullmatch(r'[a-z0-9_]{3,32}', value):
        raise DomainError('invalid_username', '登录名须为 3–32 位小写字母、数字或下划线')
    return value


def display_name(value):
    value = unicodedata.normalize('NFC', value.strip())
    if not 1 <= len(value) <= 40 or any(unicodedata.category(c).startswith('C') for c in value):
        raise DomainError('invalid_display_name', '展示名须为 1–40 字，不含控制字符')
    return value


def password(value):
    if len(value) > 1024:
        raise DomainError('invalid_password', '密码过长')
    validate_password(value)


def session(account):
    token = secrets.token_urlsafe(32)
    row = AccountSession.objects.create(account=account, token_hash=token_hash(token),
        session_version=account.session_version,
        expires_at=timezone.now() + timedelta(seconds=settings.ACCOUNT_AGE))
    return token, row


def register(name, label, secret):
    name, label = username(name), display_name(label)
    password(secret)
    encoded = make_password(secret)
    with transaction.atomic():
        if Account.objects.filter(username=name).exists():
            raise DomainError('username_exists', '登录名已被使用', 409)
        now = timezone.now()
        principal = AnonymousClient.objects.create(token_hash=token_hash(secrets.token_urlsafe(32)),
            last_seen_at=now, expires_at=now + timedelta(days=36500))
        account = Account.objects.create(username=name, display_name=label, password_hash=encoded, principal=principal)
        return session(account)


def login(name, secret):
    if len(name) > 100 or len(secret) > 1024:
        raise DomainError('invalid_credentials', '登录名或密码错误', 401)
    candidate = Account.objects.filter(username=name.strip().lower()).first()
    if candidate is None:
        make_password(secret)
        raise DomainError('invalid_credentials', '登录名或密码错误', 401)
    if not check_password(secret, candidate.password_hash) or not candidate.is_active:
        raise DomainError('invalid_credentials', '登录名或密码错误', 401)
    with transaction.atomic():
        current = Account.objects.get(pk=candidate.pk)
        if (not current.is_active or current.password_hash != candidate.password_hash
                or current.session_version != candidate.session_version):
            raise DomainError('invalid_credentials', '登录名或密码错误', 401)
        return session(current)


def require(request):
    account, current_session = request.account, request.account_session
    if not account or not current_session or not AccountSession.objects.filter(pk=current_session.pk,
        expires_at__gt=timezone.now(), account__is_active=True,
        account__session_version=current_session.session_version).exists():
        raise DomainError('login_required', '请先登录', 401)
    return account


def public(account):
    return {'id': account.pk, 'username': account.username, 'display_name': account.display_name}


def change_password(request, old, new):
    account = require(request)
    if len(old) > 1024 or not check_password(old, account.password_hash):
        raise DomainError('invalid_credentials', '当前密码错误', 403)
    password(new)
    encoded = make_password(new)
    with transaction.atomic():
        current = require(request)
        updated = Account.objects.filter(pk=current.pk, password_hash=account.password_hash).update(
            password_hash=encoded, session_version=account.session_version + 1, updated_at=timezone.now())
        if not updated:
            raise DomainError('session_changed', '账户已变更，请重新登录', 401)
        AccountSession.objects.filter(account=current).delete()
