"""QQ email possession login; delivery happens outside database write locks."""
import hashlib
import hmac
import math
import re
import secrets
from datetime import timedelta, datetime, timezone as datetime_timezone
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from . import accounts, services
from .models import Account, AccountSession, AnonymousClient, EmailChallenge


def enabled():
    if not settings.QQ_EMAIL_LOGIN:
        raise services.DomainError('email_login_disabled', '邮箱登录尚未启用', 503)


def normalize(value):
    value = value.strip().lower()
    try:
        validate_email(value)
    except ValidationError:
        raise services.DomainError('invalid_email', '请输入有效的 QQ 邮箱') from None
    if len(value) > 254 or not re.fullmatch(r'[a-z0-9][a-z0-9._+-]*@qq\.com', value):
        raise services.DomainError('invalid_email', '仅支持以 @qq.com 结尾的 QQ 邮箱')
    return value


def digest(challenge, code):
    text = f'{challenge.token}:{challenge.email}:{challenge.purpose}:{code}'
    return hmac.new(settings.SECRET_KEY.encode(), text.encode(), hashlib.sha256).hexdigest()


def rate_error(code, message, retry_after, now):
    retry_after = max(1, math.ceil(retry_after))
    reset = timezone.localtime(now + timedelta(seconds=retry_after))
    error = services.DomainError(code,
        message + f'，请在北京时间 {reset:%m月%d日 %H:%M:%S} 后重试', 429)
    error.retry_after = retry_after
    error.retry_at = reset.isoformat()
    return error


def rate_limit(scope, identifier, limit, seconds, description):
    stamp = services.time.time()
    try:
        services.rate_limit(scope, identifier, limit, seconds)
    except services.DomainError as error:
        if error.code != 'rate_limited':
            raise
        wait = (int(stamp) // seconds + 1) * seconds - stamp
        raise rate_error(scope + '_limit', description, wait,
            datetime.fromtimestamp(stamp, datetime_timezone.utc)) from None


def request_code(email, purpose, ip, account=None):
    enabled()
    email = normalize(email)
    if purpose not in ('login', 'bind'):
        raise services.DomainError('invalid_purpose', '验证用途无效')
    if purpose == 'bind' and (not account or not account.is_active or account.qq_email):
        raise services.DomainError('binding_unavailable', '请登录未绑定邮箱的原账户后操作', 409)
    rate_limit('qq_code_ip', ip, settings.QQ_CODE_IP_HOURLY_LIMIT, 3600,
        f'当前网络每小时获取验证码已达 {settings.QQ_CODE_IP_HOURLY_LIMIT} 次上限')
    now = timezone.now()
    code = f'{secrets.randbelow(1000000):06d}'
    with transaction.atomic():
        recent = EmailChallenge.objects.filter(email=email, created_at__gt=now-timedelta(seconds=60)).order_by('-created_at').first()
        if recent:
            raise rate_error('code_cooldown', '该邮箱发送间隔为 60 秒',
                60 - (now-recent.created_at).total_seconds(), now)
        rate_limit('qq_code_email', email, settings.QQ_CODE_EMAIL_DAILY_LIMIT, 86400,
            f'该邮箱本日获取验证码已达 {settings.QQ_CODE_EMAIL_DAILY_LIMIT} 次上限')
        rate_limit('qq_code_global', 'all', settings.QQ_CODE_GLOBAL_DAILY_LIMIT, 86400,
            '全站本日验证码发送额度已用完')
        EmailChallenge.objects.filter(email=email, consumed_at__isnull=True).update(consumed_at=now)
        challenge = EmailChallenge(token=secrets.token_urlsafe(32), email=email, purpose=purpose,
            account=account if purpose == 'bind' else None, expires_at=now+timedelta(minutes=5))
        challenge.code_hash = digest(challenge, code)
        challenge.save()
    try:
        sent = send_mail('ヨイツ図書館 · 邮箱验证码',
            f'你的验证码为：【{code}】（六位数字）。\n有效期：五分钟，仅可使用一次。\n如非本人操作，请忽略本邮件。请勿向他人提供验证码。',
            settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
        if sent != 1:
            raise RuntimeError('Delivery not accepted')
    except Exception:
        EmailChallenge.objects.filter(pk=challenge.pk).update(consumed_at=timezone.now())
        raise services.DomainError('email_unavailable', '验证码暂时无法发送，请稍后再试', 503) from None
    EmailChallenge.objects.filter(pk=challenge.pk, consumed_at__isnull=True).update(delivered=True)
    return {'challenge': challenge.token, 'expires_in': 300, 'retry_after': 60}


def verify(email, token, code, purpose, account=None):
    enabled()
    email = normalize(email)
    if not re.fullmatch(r'[0-9]{6}', code) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise services.DomainError('invalid_code', '验证码错误或已失效', 400)
    failure = None
    result = None
    with transaction.atomic():
        now = timezone.now()
        challenge = EmailChallenge.objects.filter(token=token, email=email, purpose=purpose).first()
        if (not challenge or not challenge.delivered or challenge.consumed_at or challenge.expires_at <= now
                or challenge.attempts >= 5 or challenge.account_id != (account.pk if account else None)):
            failure = services.DomainError('invalid_code', '验证码错误或已失效', 400)
        elif not hmac.compare_digest(challenge.code_hash, digest(challenge, code)):
            challenge.attempts += 1
            challenge.save(update_fields=['attempts'])
            failure = services.DomainError('invalid_code', '验证码错误或已失效', 400)
        else:
            if purpose == 'bind':
                current = Account.objects.get(pk=account.pk)
                if (not current.is_active or current.session_version != account.session_version
                        or current.qq_email or Account.objects.filter(qq_email=email).exists()):
                    failure = services.DomainError('binding_unavailable', '该邮箱或账户无法绑定；不会自动合并账户', 409)
                else:
                    current.qq_email = email
                    current.email_verified_at = now
                    current.session_version += 1
                    current.save(update_fields=['qq_email', 'email_verified_at', 'session_version', 'updated_at'])
                    AccountSession.objects.filter(account=current).delete()
                    result = accounts.session(current)
            else:
                current = Account.objects.filter(qq_email=email).first()
                if current and (not current.is_active or not current.email_verified_at):
                    failure = services.DomainError('login_unavailable', '该账户暂不可登录，请联系管理员', 403)
                else:
                    if current is None:
                        principal = AnonymousClient.objects.create(token_hash=services.token_hash(secrets.token_urlsafe(32)),
                            last_seen_at=now, expires_at=now+timedelta(days=36500))
                        current = Account.objects.create(username='mail_'+secrets.token_hex(12),
                            display_name='读者'+secrets.token_hex(3), password_hash=make_password(None),
                            principal=principal, qq_email=email, email_verified_at=now)
                    result = accounts.session(current)
            challenge.consumed_at = now
            challenge.save(update_fields=['consumed_at'])
    # Failure counters and consumed challenges must commit before raising.
    if failure:
        raise failure
    return result
