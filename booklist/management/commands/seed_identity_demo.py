"""Synthetic local preview only; refuses nonempty or external databases."""
import json
import secrets
import struct
import zlib
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from booklist import accounts, services, activities, cover_cache, public_booklist as books
from booklist.models import Account, Admin, Work, AnonymousClient, Activity


def cover(index):
    width, height = 180, 260
    colors = [(72, 109, 83), (116, 133, 95), (131, 112, 83), (82, 113, 121)]
    base = colors[index % len(colors)]
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            cream = (238, 233, 211)
            color = cream if (28 < x < 151 and 42 < y < 48) or (28 < x < 151 and 206 < y < 211) or (45 < x < 135 and 94 < y < 166 and (x+y+index*15) % 37 < 12) else base
            pixels.extend(color)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(bytes(pixels))) + chunk(b'IEND', b'')


class Command(BaseCommand):
    help = 'Create synthetic readers, recommendations, likes, inbox and local raster covers in an empty .runtime database.'

    def handle(self, *args, **options):
        runtime = (settings.BASE_DIR / '.runtime').resolve()
        data_dir = settings.DATA_DIR.resolve()
        if settings.ENV != 'development' or runtime not in data_dir.parents:
            raise CommandError('Demo seeding requires development and a dedicated subdirectory inside this worktree .runtime')
        credentials = data_dir / 'preview-credentials.json'
        with transaction.atomic():
            if any(model.objects.exists() for model in (Account, Admin, Work, AnonymousClient, Activity)):
                raise CommandError('Database is not empty. Refusing to modify existing data.')
            passwords = {name: secrets.token_urlsafe(18) for name in ('alice', 'bob', 'carol', 'demo_owner')}
            readers = {}
            for name, label in [('alice', '青禾'), ('bob', '林间'), ('carol', '小满')]:
                _, session = accounts.register(name, label, passwords[name])
                readers[name] = session.account.principal
            admin = services.initialize_admin('demo_owner', passwords['demo_owner'])
            now = timezone.now()
            period = Activity.objects.create(title='秋日共读 · 演示活动', theme='合成书目与账户，仅供本地演示',
                status='active', start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1),
                scheduled_end_at=now+timedelta(days=30), created_by=admin)
            titles = ['山间来信', '月光图书馆', '在森林尽头', '沿着河流读下去']
            for index, title in enumerate(titles):
                work = Work.objects.create(original_submitted_title=title,
                    source_metadata={'identity_demo': True, 'cover_synthetic': True})
                # Synthetic cache lookup keys, never fetched from the network.
                url = f'https://img.wenku8.com/image/900/{900001+index}/{900001+index}s.png'
                cover_cache.store(url, cover(index), 'image/png')
                work.wenku8_cover_url = url
                work.save(update_fields=['wenku8_cover_url', 'updated_at'])
                from booklist import user_metadata
                user_metadata.initialize(work, readers['alice'], {'author': '合成作者',
                    'description': f'《{title}》是本地身份版演示书目，用于验证推荐、共同推荐和消息流程。',
                    'tags': ['合成书目', ['书信', '奇幻', '自然', '旅行'][index]]})
                for name in ('alice', 'bob') if index < 2 else ('alice',):
                    activities.submit(period.pk, readers[name], {'work_id': work.pk, 'title': title,
                        'reason': '适合慢慢读的一本书。' if name == 'alice' else '我也推荐，留给下一个周末。',
                        'confirmed_unmatched': False, 'copy_public_reason': True})
                books.set_relation(work, readers['carol'], 'like', True)
                if index < 2:
                    activities.like(period.pk, work.pk, readers['carol'], True)
            credentials.write_text(json.dumps({'warning': 'Local synthetic demo only. Not production credentials.',
                'readers': {name: {'display_name': label, 'password': passwords[name]} for name, label in [('alice','青禾'),('bob','林间'),('carol','小满')]},
                'admin': {'username': 'demo_owner', 'password': passwords['demo_owner']},
                'activity_id': period.pk}, ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(f'Synthetic demo created. Credentials: {credentials}')
