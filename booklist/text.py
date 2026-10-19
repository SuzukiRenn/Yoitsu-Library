from django.core.exceptions import ValidationError

WHITESPACE = '\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
LIMITS = {'reason': 200, 'title': 300, 'alias': 300, 'activity_title': 100, 'theme': 500, 'description': 10000}


def normalize(value, field='reason', required=False):
    if not isinstance(value, str) or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValidationError('文本必须为有效 Unicode 字符串')
    result = value.replace('\r\n', '\n').replace('\r', '\n').strip(WHITESPACE)
    if (required and not result) or len(result) > LIMITS[field]:
        raise ValidationError('文本为空或超出长度限制')
    return result
