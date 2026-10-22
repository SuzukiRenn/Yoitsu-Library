"""Bounded local cover cache. Web requests only read files, never fetch remote URLs."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
from urllib.parse import urlsplit
import zlib
from django.conf import settings
from django.http import FileResponse, HttpResponse, HttpResponseNotModified

MAX_BYTES = 1024 * 1024
MAX_DIMENSION = 4096
NAME = re.compile(r'[0-9a-f]{64}\.(?:jpg|png)\Z')


def source_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or url.netloc != 'img.wenku8.com' or url.query or url.fragment:
            return None
        match = re.fullmatch(r'/image/([0-9]+)/([1-9][0-9]*)/\2s\.(jpg|jpeg|png)', url.path)
        if match and int(match[1]) == int(match[2]) // 1000:
            return 'https://img.wenku8.com'+url.path
    except ValueError:
        pass
    return None


def filename(value):
    url = source_url(value)
    if url is None:
        return None
    extension = 'png' if url.endswith('.png') else 'jpg'
    return hashlib.sha256(url.encode()).hexdigest()+'.'+extension


def cache_root():
    return Path(settings.COVER_CACHE_DIR)


def cache_info(name):
    if not NAME.fullmatch(name):
        return None
    root = cache_root()
    image, metadata = root/name, root/(name+'.json')
    if image.is_symlink() or metadata.is_symlink() or not image.is_file() or not metadata.is_file():
        return None
    try:
        if metadata.stat().st_size > 4096:
            return None
        info = json.loads(metadata.read_text(encoding='utf-8'))
        expected = filename(info['source_url'])
        if info.get('versioned') is True:
            expected = version_name(info['source_url'], info['sha256'])
        if expected != name or image.stat().st_size != info['bytes'] or not 0 < info['bytes'] <= MAX_BYTES:
            return None
        if not re.fullmatch(r'[0-9a-f]{64}', info['sha256']):
            return None
        return info
    except (OSError, ValueError, KeyError, TypeError):
        return None


def local_url(value):
    if isinstance(value, str) and value.startswith('/media/covers/'):
        name = value[len('/media/covers/'):]
        return value if NAME.fullmatch(name) and cache_info(name) else None
    name = filename(value)
    return '/media/covers/'+name if name and cache_info(name) else None


def version_name(value, digest):
    url = source_url(value)
    if url is None or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        return None
    extension = 'png' if url.endswith('.png') else 'jpg'
    return hashlib.sha256((url+'\n'+digest).encode()).hexdigest()+'.'+extension


def validate_image(raw, extension, media_type):
    """Check bounded raster structure; reject HTML, SVG, truncation and huge dimensions."""
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('image_size_invalid')
    width = height = 0
    if extension == 'png':
        if media_type != 'image/png' or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('image_type_invalid')
        offset = 8; data_seen = False; ended = False
        while offset + 12 <= len(raw):
            length = int.from_bytes(raw[offset:offset+4], 'big')
            kind = raw[offset+4:offset+8]; end = offset+12+length
            if end > len(raw) or zlib.crc32(raw[offset+4:end-4]) & 0xffffffff != int.from_bytes(raw[end-4:end], 'big'):
                raise ValueError('image_structure_invalid')
            if offset == 8:
                if kind != b'IHDR' or length != 13:
                    raise ValueError('image_structure_invalid')
                width, height = struct.unpack('>II', raw[offset+8:offset+16])
            if kind == b'IDAT': data_seen = True
            if kind == b'IEND':
                ended = length == 0 and end == len(raw)
                break
            offset = end
        if not ended or not data_seen:
            raise ValueError('image_structure_invalid')
    else:
        if media_type != 'image/jpeg' or not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
            raise ValueError('image_type_invalid')
        offset = 2; scan = False
        while offset < len(raw)-2:
            if raw[offset] != 255:
                raise ValueError('image_structure_invalid')
            while offset < len(raw) and raw[offset] == 255: offset += 1
            if offset >= len(raw): break
            marker = raw[offset]; offset += 1
            if marker == 0 or marker in (0xd8, 0xd9):
                raise ValueError('image_structure_invalid')
            if marker == 1 or 0xd0 <= marker <= 0xd7: continue
            if offset+2 > len(raw): break
            length = int.from_bytes(raw[offset:offset+2], 'big')
            if length < 2 or offset+length > len(raw):
                raise ValueError('image_structure_invalid')
            if marker in (0xc0, 0xc1, 0xc2):
                if length < 8: raise ValueError('image_structure_invalid')
                height, width = struct.unpack('>HH', raw[offset+3:offset+7])
            if marker == 0xda:
                scan = True; break
            offset += length
        if not scan:
            raise ValueError('image_structure_invalid')
    if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION):
        raise ValueError('image_dimensions_invalid')
    return {'width': width, 'height': height}


def atomic_file(path, data):
    descriptor, temp = tempfile.mkstemp(prefix='.cover-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def store(value, raw, media_type):
    name = filename(value)
    if name is None: raise ValueError('cover_origin_not_allowed')
    dimensions = validate_image(raw, name.rsplit('.', 1)[1], media_type)
    root = cache_root(); root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or (root/name).is_symlink(): raise ValueError('linked_cache_not_allowed')
    info = {'source_url': source_url(value), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), **dimensions}
    atomic_file(root/name, raw)
    atomic_file(root/(name+'.json'), json.dumps(info, ensure_ascii=True).encode())
    return info


def store_version(value, raw, media_type):
    """Preserve the original URL cache for legacy activity snapshots; add immutable revisions."""
    original = filename(value)
    if original is None: raise ValueError('cover_origin_not_allowed')
    dimensions = validate_image(raw, original.rsplit('.', 1)[1], media_type)
    digest = hashlib.sha256(raw).hexdigest()
    name = version_name(value, digest)
    root = cache_root(); root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or (root/name).is_symlink(): raise ValueError('linked_cache_not_allowed')
    base_info = cache_info(original)
    if base_info is None:
        # Never overwrite a known old cover when source bytes change at the same URL.
        if (root/original).exists() or (root/(original+'.json')).exists():
            raise ValueError('existing_cover_requires_review')
        store(value, raw, media_type)
    elif hashlib.sha256((root/original).read_bytes()).hexdigest() != base_info['sha256']:
        raise ValueError('existing_cover_hash_mismatch')
    info = {'source_url': source_url(value), 'bytes': len(raw), 'sha256': digest,
            **dimensions, 'versioned': True}
    if cache_info(name) is None:
        if (root/name).exists() or (root/(name+'.json')).exists():
            raise ValueError('existing_cover_requires_review')
        atomic_file(root/name, raw)
        atomic_file(root/(name+'.json'), json.dumps(info, ensure_ascii=True).encode())
    elif hashlib.sha256((root/name).read_bytes()).hexdigest() != digest:
        raise ValueError('existing_cover_hash_mismatch')
    return {**info, 'name': name}


def serve(request, name):
    if request.method not in ('GET', 'HEAD'):
        response = HttpResponse(status=405); response['Allow'] = 'GET, HEAD'; return response
    info = cache_info(name)
    if info is None: return HttpResponse(status=404)
    etag = '"'+info['sha256']+'"'
    if request.headers.get('If-None-Match') in (etag, '*'):
        response = HttpResponseNotModified()
    else:
        try: stream = (cache_root()/name).open('rb')
        except OSError: return HttpResponse(status=404)
        response = FileResponse(stream, content_type='image/png' if name.endswith('.png') else 'image/jpeg')
    response['ETag'] = etag
    response['Cache-Control'] = 'public, max-age=86400'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
