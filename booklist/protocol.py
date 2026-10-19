from datetime import datetime, timezone
from django.core.exceptions import ValidationError


def utc_milliseconds(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError('必须提供带时区的时间')
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def pagination(page=1, page_size=20):
    if isinstance(page, bool) or isinstance(page_size, bool):
        raise ValidationError('分页参数必须为整数')
    try:
        if str(int(page)) != str(page) or str(int(page_size)) != str(page_size):
            raise ValueError
        page, page_size = int(page), int(page_size)
    except (ValueError, TypeError, OverflowError):
        raise ValidationError('分页参数必须为整数') from None
    if page < 1 or not 1 <= page_size <= 100:
        raise ValidationError('分页参数超出范围')
    return (page - 1) * page_size, page_size
