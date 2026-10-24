"""Shared, validated ordering for public and activity shelves."""
from urllib.parse import urlencode
from django.core.exceptions import ValidationError
from .shelf_filters import personal_parameters, tag_mode_parameters

PERIODS = (('all', '全部时间'), ('month', '近一个月'), ('week', '近一周'))

def validate_period(value):
    if value not in dict(PERIODS):
        raise ValidationError('时间范围无效')
    return value


OPTIONS = (('recommendations', '推荐最多'), ('likes', '点赞最多'), ('newest', '最新加入'))


def validate(value):
    if value not in dict(OPTIONS):
        raise ValidationError('排序方式无效')
    return value


def controls(result):
    parameters = personal_parameters(result) + tag_mode_parameters(result) + [('tag', tag) for tag in result.get('selected_tags', [])]
    if result.get('query'):parameters.append(('q',result['query']))
    parameters.append(('page_size', result['page_size']))
    return [{'label': label, 'active': value == result['sort'],
             'url': '?' + urlencode(parameters + [('sort', value)] + ([('period', result['period'])] if 'period' in result else []))}
            for value, label in OPTIONS]


def period_controls(result):
    parameters = personal_parameters(result) + tag_mode_parameters(result) + [('tag', tag) for tag in result.get('selected_tags', [])]
    if result.get('query'):parameters.append(('q',result['query']))
    parameters += [('page_size', result['page_size']), ('sort', result['sort'])]
    return [{'label': label, 'active': value == result['period'],
             'url': '?' + urlencode(parameters + [('period', value)])}
            for value, label in PERIODS]
