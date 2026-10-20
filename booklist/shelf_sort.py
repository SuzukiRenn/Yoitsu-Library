"""Shared, validated ordering for public and activity shelves."""
from urllib.parse import urlencode
from django.core.exceptions import ValidationError

OPTIONS = (('recommendations', '推荐最多'), ('likes', '点赞最多'), ('newest', '最新加入'))


def validate(value):
    if value not in dict(OPTIONS):
        raise ValidationError('排序方式无效')
    return value


def controls(result):
    parameters = [('tag', tag) for tag in result.get('selected_tags', [])]
    parameters.append(('page_size', result['page_size']))
    return [{'label': label, 'active': value == result['sort'],
             'url': '?' + urlencode(parameters + [('sort', value)])}
            for value, label in OPTIONS]
