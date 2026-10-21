"""Public shelf periods. Activities deliberately do not use this protocol."""
from datetime import timedelta
from urllib.parse import urlencode
from django.core.exceptions import ValidationError

OPTIONS = (('all', '全部时间'), ('month', '近一个月'), ('week', '近一周'))
DAYS = {'month': 30, 'week': 7}


def validate(value, sort):
    if value not in dict(OPTIONS):
        raise ValidationError('时间范围无效')
    return 'all' if sort == 'newest' else value


def cutoff(period, now):
    return now - timedelta(days=DAYS[period]) if period in DAYS else None


def controls(result):
    parameters = [('tag', tag) for tag in result['selected_tags']]
    parameters += [('page_size', result['page_size']), ('sort', result['sort'])]
    return [{'value': value, 'label': label, 'active': value == result['period'],
             'url': '?' + urlencode(parameters + [('period', value)])} for value, label in OPTIONS]
