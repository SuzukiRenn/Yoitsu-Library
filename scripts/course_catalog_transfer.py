"""Move the visible identity catalog into an existing, isolated course volume.

Run from the application root inside each image. Both phases work offline.
Export uses a SQLite read-only WAL snapshot and a fresh bundle directory.
The bundle contains manifest.json, manifest.sha256, and covers/{cache,rankings}/.
A live WAL database mounted read-only must have readable WAL and SHM sidecars.
The target already needs the matching approved rankings assets in its image.
Import is append-only and uses the application's SQLite IMMEDIATE transaction.
"""

from argparse import ArgumentParser
from collections import defaultdict
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit


ORIGIN = "identity-visible-catalog"
MARKER = "course_catalog_source_work_id"
MEDIA_PREFIX = "/media/covers/"
STATIC_PREFIX = "/static/rankings/covers/"
ASSET_KEY = re.compile(r"(cache|rankings)/([a-f0-9]{64}\.(?:jpg|png))")
SHA256 = re.compile(r"[a-f0-9]{64}")
MAX_WORKS = 20000
MAX_MANIFEST = 64 * 1024 * 1024
DETAIL_FIELDS = {"publisher_category", "status", "last_updated", "word_count"}
TEXT_FIELDS = DETAIL_FIELDS - {"word_count"} | {"japanese_title"}
PROVIDERS = {"wenku8", "bilinovel", "konorano_rankings"}
EDITIONS = {"web", "book", "other"}


class TransferError(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise TransferError(code)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_file(path, limit):
    need(path.is_file() and not path.is_symlink(), "required_file_missing_or_linked")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    need(len(raw) <= limit, "file_too_large")
    return raw


def is_int(value):
    return type(value) is int and 0 < value <= 2**63 - 1


def is_text(value, limit):
    return isinstance(value, str) and len(value) <= limit and "\0" not in value


def safe_https(value):
    if not is_text(value, 2048) or any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in value):
        return False
    try:
        url = urlsplit(value)
        return bool(url.scheme == "https" and url.hostname and url.username is None
                    and url.password is None and not url.query and not url.fragment
                    and url.port in (None, 443))
    except ValueError:
        return False


def public_metadata(source):
    need(isinstance(source, dict), "invalid_source_metadata")
    result = {key: source[key] for key in TEXT_FIELDS if is_text(source.get(key), 300) and source[key]}
    count = source.get("word_count")
    if is_int(count) or (type(count) is int and count == 0) or (is_text(count, 50) and count.isdigit()):
        result["word_count"] = count
    for key, allowed in (("edition", EDITIONS), ("source_provider", PROVIDERS)):
        if isinstance(source.get(key), str) and source[key] in allowed:
            result[key] = source[key]
    if is_int(source.get("bili_id")):
        result["bili_id"] = source["bili_id"]
    ranking = source.get("ranking_key")
    if is_text(ranking, 64) and re.fullmatch("[a-z0-9-]+", ranking):
        result["ranking_key"] = ranking
    if result.get("source_provider") and safe_https(source.get("source_url")):
        result["source_url"] = source["source_url"]
    return result


def work_keys(wenku8_id, metadata):
    return {(key, value) for key, value in (
        ("wenku8", wenku8_id), ("bili", metadata.get("bili_id")), ("ranking", metadata.get("ranking_key")),
    ) if value is not None}


def manual_display_metadata(works):
    from booklist.models import UserWorkMetadata

    ids = [work.pk for work in works if work.wenku8_book_id is None or work.source_metadata.get("preserve_user_description")]
    rows = UserWorkMetadata.objects.filter(work_id__in=ids, archived_at__isnull=True).only("work_id", "author", "description")
    return {row.work_id: row for row in rows}


def display_text(work, manual):
    # The identity image still implements this projection inline in serialize_works().
    author = work.author_override if work.author_overridden else work.wenku8_author or ""
    if manual and work.wenku8_book_id is None and not work.author_overridden:
        author = manual.author or author
    description = work.description_override if work.description_overridden else (
        manual.description if manual and manual.description else work.wenku8_description or ""
    )
    return work.display_title, author, description


def work_rows(works):
    """Batch public metadata and effective tags, without loading actors or interactions."""
    from booklist.models import TagSuppression, WorkAlias, WorkTag

    ids = [work.pk for work in works]
    manuals = manual_display_metadata(works)
    aliases = defaultdict(set)
    tags = defaultdict(set)
    for work_id, alias in WorkAlias.objects.filter(
        work_id__in=ids, archived_at__isnull=True,
    ).values_list("work_id", "alias"):
        aliases[work_id].add(alias)
    bans = set(TagSuppression.objects.filter(
        work_id__in=ids, is_active=True,
    ).values_list("work_id", "tag_id", "scope"))
    for work_id, tag_id, name, origin in WorkTag.objects.filter(
        work_id__in=ids, state="active", tag__is_active=True, tag__merged_into__isnull=True,
    ).values_list("work_id", "tag_id", "tag__name", "source_type"):
        if (work_id, tag_id, "all") not in bans and (
            origin != "source" or (work_id, tag_id, "source") not in bans
        ):
            tags[work_id].add(name)
    for work in works:
        title, author, description = display_text(work, manuals.get(work.pk))
        yield work, title, author, description, sorted(aliases[work.pk]), sorted(tags[work.pk])


def verified_image(raw, name):
    from booklist import cover_cache

    extension = name.rsplit(".", 1)[-1]
    try:
        return cover_cache.validate_image(raw, extension, "image/png" if extension == "png" else "image/jpeg")
    except ValueError as error:
        raise TransferError("invalid_cover_image") from error


def bundle_cover(work, stage, assets):
    from booklist import catalog, cover_cache, rankings
    from django.conf import settings

    pinned = catalog.versioned_cover(work)
    displayed = pinned or catalog.cover_url(
        work.cover_url_override if work.cover_url_overridden else work.wenku8_cover_url,
    )
    cover = {"value": "", "asset": None, "overridden": work.cover_url_overridden, "pinned": bool(pinned)}
    if not displayed:
        return cover
    if displayed.startswith(MEDIA_PREFIX):
        name = displayed.removeprefix(MEDIA_PREFIX)
        key = "cache/" + name
        if key not in assets:
            info = cover_cache.cache_info(name)
            need(info is not None and not cover_cache.cache_root().is_symlink(), "source_cache_changed")
            raw = read_file(cover_cache.cache_root() / name, cover_cache.MAX_BYTES)
            need(digest(raw) == info["sha256"], "source_cache_checksum_mismatch")
            assets[key] = {"sha256": digest(raw), "bytes": len(raw), "source_url": info["source_url"],
                           **verified_image(raw, name), "versioned": info.get("versioned") is True}
            (stage / "covers" / key).write_bytes(raw)
        cover.update(value=assets[key]["source_url"] if pinned else displayed, asset=key)
    elif displayed.startswith(STATIC_PREFIX):
        name = displayed.removeprefix(STATIC_PREFIX)
        key = "rankings/" + name
        if key not in assets:
            raw = read_file(settings.BASE_DIR / "static/rankings/covers" / name, cover_cache.MAX_BYTES)
            sha256 = digest(raw)
            need(sha256 == rankings.dataset()["coverHashes"][name], "source_rankings_checksum_mismatch")
            verified_image(raw, name)
            assets[key] = {"sha256": sha256, "bytes": len(raw)}
            (stage / "covers" / key).write_bytes(raw)
        cover.update(value=displayed, asset=key)
    elif safe_https(displayed):
        cover["value"] = displayed
    return cover


def validate_rows(rows, assets):
    need(isinstance(rows, list) and 0 < len(rows) <= MAX_WORKS, "invalid_work_count")
    need(isinstance(assets, dict) and len(assets) <= len(rows), "invalid_asset_count")
    ids, keys, used_assets = set(), set(), set()
    for row in rows:
        need(isinstance(row, dict) and row.keys() == {
            "source_id", "wenku8_id", "title", "author", "description", "aliases",
            "tags", "metadata", "cover", "overrides",
        }, "invalid_work_fields")
        pk, wenku8_id = row["source_id"], row["wenku8_id"]
        need(is_int(pk) and pk not in ids, "duplicate_or_invalid_source_work_id")
        ids.add(pk)
        need(wenku8_id is None or is_int(wenku8_id), "invalid_wenku8_id")
        need(is_text(row["title"], 300) and row["title"].strip() and is_text(row["author"], 300)
             and is_text(row["description"], 10000), "invalid_display_text")
        for field in ("aliases", "tags"):
            values = row[field]
            need(isinstance(values, list) and len(values) <= 1000 and
                 all(is_text(value, 300) and value.strip() for value in values) and
                 len(set(values)) == len(values), "invalid_" + field)
        metadata = row["metadata"]
        need(public_metadata(metadata) == metadata, "invalid_public_metadata")
        source_keys = work_keys(wenku8_id, metadata)
        need(not (source_keys & keys), "duplicate_source_work_identity")
        keys.update(source_keys)
        flags = row["overrides"]
        need(isinstance(flags, dict) and flags.keys() == {"title", "author", "description"} and
             all(type(flag) is bool for flag in flags.values()), "invalid_override_flags")
        cover = row["cover"]
        need(isinstance(cover, dict) and cover.keys() == {"asset", "value", "overridden", "pinned"} and
             type(cover["pinned"]) is bool and type(cover["overridden"]) is bool, "invalid_cover_reference")
        asset, url = cover["asset"], cover["value"]
        if asset is None:
            need(not cover["pinned"] and (url == "" or safe_https(url)), "invalid_remote_cover")
            continue
        need(isinstance(asset, str) and ASSET_KEY.fullmatch(asset) and asset in assets, "invalid_cover_asset")
        kind, name = asset.split("/")
        if cover["pinned"]:
            info = assets[asset]
            need(kind == "cache" and isinstance(info, dict) and info.get("versioned") is True and
                 not cover["overridden"] and url == info.get("source_url") and safe_https(url),
                 "invalid_versioned_cover")
        else:
            need(url == (STATIC_PREFIX if kind == "rankings" else MEDIA_PREFIX) + name, "invalid_local_cover")
        used_assets.add(asset)
    need(used_assets == assets.keys(), "unreferenced_cover_asset")


def export_bundle(bundle, db, *, source_connection):
    from booklist import catalog
    from django.db.backends.signals import connection_created

    bundle.mkdir(mode=0o700, parents=False, exist_ok=True)
    need(not any(bundle.iterdir()), "export_bundle_must_be_empty")
    # Prevent the usual connection signal from issuing PRAGMA journal_mode=WAL.
    connection_created.disconnect(dispatch_uid="booklist_sqlite")
    source_connection.close()
    source_connection.settings_dict["NAME"] = db.as_uri() + "?mode=ro"
    with TemporaryDirectory(prefix=".export-", dir=bundle) as temp:
        stage = Path(temp)
        for kind in ("cache", "rankings"):
            (stage / "covers" / kind).mkdir(parents=True)
        assets, rows = {}, []
        try:
            with closing(source_connection.cursor()) as cursor:
                cursor.execute("PRAGMA query_only=ON")
                cursor.execute("BEGIN")
                works = list(catalog.visible_works().order_by("pk")[:MAX_WORKS + 1])
                need(0 < len(works) <= MAX_WORKS, "invalid_work_count")
                for work, title, author, description, aliases, tags in work_rows(works):
                    rows.append({
                        "source_id": work.pk, "title": title, "author": author, "description": description,
                        "wenku8_id": work.wenku8_book_id, "aliases": aliases, "tags": tags,
                        "metadata": public_metadata(work.source_metadata),
                        "cover": bundle_cover(work, stage, assets),
                        "overrides": {
                            "title": work.title_overridden,
                            "author": work.author_overridden or author != (work.wenku8_author or ""),
                            "description": work.description_overridden or description != (work.wenku8_description or ""),
                        },
                    })
        finally:
            # Closing the read-only connection ends the snapshot without touching journal mode.
            source_connection.close()
        validate_rows(rows, assets)
        raw = encoded({"format": 1, "source": ORIGIN, "works": rows, "assets": assets})
        need(len(raw) <= MAX_MANIFEST, "manifest_too_large")
        sha256 = digest(raw)
        (stage / "manifest.json").write_bytes(raw)
        (stage / "manifest.sha256").write_text(sha256 + "\n", encoding="ascii")
        # The checksum file is the last published member. Use a fresh bundle after an interrupted export.
        for name in ("covers", "manifest.json", "manifest.sha256"):
            (stage / name).rename(bundle / name)
    return {"manifest_sha256": sha256, "works": len(rows), "assets": len(assets)}


def load_bundle(bundle):
    from booklist import cover_cache

    raw = read_file(bundle / "manifest.json", MAX_MANIFEST)
    sha256 = digest(raw)
    need(read_file(bundle / "manifest.sha256", 128).decode("ascii").strip() == sha256, "manifest_checksum_mismatch")
    doc = json.loads(raw)
    need(isinstance(doc, dict) and doc.keys() == {"format", "source", "works", "assets"} and
         type(doc["format"]) is int and doc["format"] == 1 and doc["source"] == ORIGIN, "invalid_bundle_format")
    validate_rows(doc["works"], doc["assets"])
    covers = bundle / "covers"
    need(not any(path.is_symlink() for path in (covers, covers / "cache", covers / "rankings")), "linked_bundle_path")
    for key, info in doc["assets"].items():
        kind, name = key.split("/")
        need(isinstance(info, dict) and is_int(info.get("bytes")) and isinstance(info.get("sha256"), str) and
             SHA256.fullmatch(info["sha256"]), "invalid_cover_metadata")
        image = checked_asset(bundle, key, info)
        dimensions = verified_image(image, name)
        if kind == "rankings":
            need(info.keys() == {"sha256", "bytes"}, "invalid_rankings_cover_metadata")
            continue
        need(info.keys() == {"sha256", "bytes", "width", "height", "source_url", "versioned"} and
             all(info[field] == value for field, value in dimensions.items()) and
             type(info["versioned"]) is bool, "invalid_cache_metadata")
        url = info["source_url"]
        expected = cover_cache.version_name(url, info["sha256"]) if info["versioned"] else cover_cache.filename(url)
        need(name == expected and url == cover_cache.source_url(url), "cache_origin_mismatch")
    return doc, sha256


def checked_asset(bundle, key, info):
    from booklist.cover_cache import MAX_BYTES

    image = read_file(bundle / "covers" / key, MAX_BYTES)
    need(len(image) == info["bytes"] and digest(image) == info["sha256"], "bundle_cover_checksum_mismatch")
    return image


def plan_works(rows):
    from booklist import catalog
    from booklist.editions import edition_info
    from booklist.models import Work

    existing = list(Work.objects.order_by("pk")[:MAX_WORKS + 1])
    need(len(existing) <= MAX_WORKS, "target_work_limit")
    visible = set(catalog.visible_works().values_list("pk", flat=True))
    manual = manual_display_metadata(existing)
    by_key, by_marker, by_title = defaultdict(list), defaultdict(list), defaultdict(list)
    target_keys = {}
    for work in existing:
        meta = public_metadata(work.source_metadata)
        keys = target_keys[work.pk] = work_keys(work.wenku8_book_id, meta)
        for key in keys:
            by_key[key].append(work.pk)
        marker = work.source_metadata.get(MARKER)
        if is_int(marker):
            by_marker[marker].append(work.pk)
        title, author, description = display_text(work, manual.get(work.pk))
        by_title[catalog.search_key(title)].append((work.pk, author, description, edition_info(meta)["edition"]))
    claimed, pending = set(), []
    for row in rows:
        pk = row["source_id"]
        keys = work_keys(row["wenku8_id"], row["metadata"])
        matches = set(by_marker[pk])
        for key in keys:
            matches.update(by_key[key])
        if not matches:
            edition = edition_info(row["metadata"])["edition"]
            author_key = catalog.search_key(row["author"])
            for candidate, author, description, target_edition in by_title[catalog.search_key(row["title"])]:
                other_author = catalog.search_key(author)
                if author_key and other_author and author_key != other_author:
                    continue
                if edition != target_edition and "unknown" not in (edition, target_edition):
                    continue
                equivalent_manual = (not keys and not target_keys[candidate] and author_key and
                                     author_key == other_author and row["description"] and
                                     row["description"] == description and edition == target_edition)
                need(equivalent_manual, f"target_title_collision:{pk}")
                matches.add(candidate)
        need(len(matches) <= 1, f"ambiguous_target_work:{pk}")
        if not matches:
            pending.append(row)
            continue
        match = matches.pop()
        need(match in visible and match not in claimed, f"target_work_conflict:{pk}")
        known = dict(target_keys[match])
        need(all(known.get(kind, value) == value for kind, value in keys), f"target_source_identity_conflict:{pk}")
        claimed.add(match)
    return pending


def plan_tags(rows):
    from booklist.models import Tag
    from booklist.user_metadata import tag_key

    by_name, selected = defaultdict(list), {}
    for tag in Tag.objects.all().order_by("pk"):
        by_name[tag_key(tag.name)].append(tag)
    for name in sorted({name for row in rows for name in row["tags"]}):
        key = tag_key(name)
        candidates = by_name[key]
        active = [tag for tag in candidates if tag.is_active and tag.merged_into_id is None]
        need(len(active) <= 1 and (active or not candidates), "target_tag_conflict")
        if active:
            # Matching a normalized spelling with different public text would silently change the imported tag.
            need(active[0].name == name, "target_tag_spelling_conflict")
        need(key not in selected or selected[key][0] == name, "source_tag_spelling_conflict")
        selected[key] = (name, active[0] if active else None)
    return selected


def verify_rankings_cover(name, info):
    from booklist import cover_cache, rankings
    from django.conf import settings

    url = STATIC_PREFIX + name
    need(rankings.curated_cover(url) == url and rankings.dataset()["coverHashes"][name] == info["sha256"],
         "target_rankings_asset_requires_matching_release")
    raw = read_file(settings.BASE_DIR / "static/rankings/covers" / name, cover_cache.MAX_BYTES)
    need(len(raw) == info["bytes"] and digest(raw) == info["sha256"], "target_rankings_checksum_mismatch")


def plan_assets(rows, assets):
    from booklist import cover_cache

    root = cover_cache.cache_root()
    paths = []
    need(not root.is_symlink(), "linked_cover_cache")
    used = {row["cover"]["asset"] for row in rows} - {None}
    for key in sorted(used):
        kind, name = key.split("/")
        info = assets[key]
        if kind == "rankings":
            verify_rankings_cover(name, info)
            continue
        image, meta = root / name, root / (name + ".json")
        cache_metadata = {k: v for k, v in info.items() if k != "versioned" or v}
        if image.exists() or image.is_symlink():
            raw = read_file(image, cover_cache.MAX_BYTES)
            need(len(raw) == info["bytes"] and digest(raw) == info["sha256"], "target_cache_cover_conflict")
        else:
            paths.append((image, key, None))
        if meta.exists() or meta.is_symlink():
            need(json.loads(read_file(meta, 4096)) == cache_metadata, "target_cache_metadata_conflict")
        else:
            paths.append((meta, key, encoded(cache_metadata)))
    return paths


def create_works(rows, tags, assets):
    from booklist.catalog import search_key
    from booklist.models import Tag, Work, WorkAlias, WorkTag
    from booklist.user_metadata import tag_key

    new_tags = [Tag(name=name, normalized_name=key) for key, (name, tag) in tags.items() if tag is None]
    Tag.objects.bulk_create(new_tags, batch_size=200)
    by_name = {tag.normalized_name: tag for tag in new_tags}
    by_name.update({key: tag for key, (_, tag) in tags.items() if tag is not None})
    works = []
    for row in rows:
        cover, flags, wenku8_id = row["cover"], row["overrides"], row["wenku8_id"]
        meta = {**row["metadata"], MARKER: row["source_id"]}
        if cover["pinned"]:
            name = cover["asset"].split("/")[1]
            meta["cover_cache_version"] = {**assets[cover["asset"]], "name": name}
        value = cover["value"]
        if cover["asset"] and value.startswith(MEDIA_PREFIX) and not cover["overridden"]:
            info = assets[cover["asset"]]
            if not info["versioned"]:
                value = info["source_url"]
        works.append(Work(
            wenku8_book_id=wenku8_id, identity_status="matched" if wenku8_id else "unmatched",
            wenku8_detail_url=f"https://www.wenku8.net/book/{wenku8_id}.htm" if wenku8_id else None,
            source_metadata=meta, original_submitted_title=row["title"] if not wenku8_id else None,
            wenku8_title=row["title"] if wenku8_id else None, title_overridden=flags["title"],
            title_override=row["title"] if flags["title"] else "",
            author_overridden=flags["author"], author_override=row["author"] if flags["author"] else "",
            wenku8_author=row["author"] if not flags["author"] else None,
            description_overridden=flags["description"],
            description_override=row["description"] if flags["description"] else "",
            wenku8_description=row["description"] if not flags["description"] else None,
            cover_url_overridden=cover["overridden"], cover_url_override=value if cover["overridden"] else "",
            wenku8_cover_url=value if not cover["overridden"] else None,
        ))
    Work.objects.bulk_create(works, batch_size=100)
    WorkAlias.objects.bulk_create([
        WorkAlias(work=work, alias=alias, normalized_alias=search_key(alias))
        for work, row in zip(works, rows, strict=True) for alias in row["aliases"]
    ], batch_size=200)
    WorkTag.objects.bulk_create([
        WorkTag(work=work, tag=by_name[tag_key(name)], source_type="source")
        for work, row in zip(works, rows, strict=True) for name in row["tags"]
    ], batch_size=200)
    return len(new_tags)


def install_noreplace(staged, destination):
    if os.name == "nt":
        # Windows rename fails if the destination exists.
        staged.rename(destination)
    else:
        os.link(staged, destination)


def publish_files(paths, assets, bundle, created):
    from booklist.cover_cache import cache_root

    root = cache_root()
    root.mkdir(mode=0o700, exist_ok=True)
    need(not root.is_symlink(), "linked_cover_cache")
    with TemporaryDirectory(prefix=".catalog-transfer-", dir=root) as temp:
        for path, key, metadata in paths:
            staged = Path(temp) / path.name
            raw = checked_asset(bundle, key, assets[key]) if metadata is None else metadata
            with staged.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            identity = staged.stat()
            install_noreplace(staged, path)
            created.append((path, identity))
        if os.name == "posix":
            # Commit DB references only after the directory entries are durable.
            for folder in (root, root.parent):
                fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)


def import_bundle(bundle, *, dry_run=False):
    from django.db import transaction

    doc, sha256 = load_bundle(bundle)
    assets, rows = doc["assets"], doc["works"]
    created = []
    with transaction.atomic(durable=True):
        pending = plan_works(rows)
        tags = plan_tags(pending)
        paths = plan_assets(pending, assets)
        new_tags = sum(tag is None for _, tag in tags.values())
        if not dry_run:
            try:
                create_works(pending, tags, assets)
                if paths:
                    publish_files(paths, assets, bundle, created)
            except BaseException:
                # Clean up before releasing the SQLite write lock so a retry cannot adopt
                # these files during rollback. In a crash or unknown commit outcome, a
                # later import may adopt verified assets without overwriting them.
                for path, identity in reversed(created):
                    if path.exists() and os.path.samestat(identity, path.lstat()):
                        path.unlink()
                raise
    return {"manifest_sha256": sha256, "works": len(rows), "assets": len(assets),
            "dry_run": dry_run, "created_works": 0 if dry_run else len(pending),
            "would_create_works": len(pending), "existing_works": len(rows) - len(pending),
            "created_tags": 0 if dry_run else new_tags, "cache_files_added": len(created),
            "cache_files_needed": len(paths)}


def dispatch(args):
    import django
    from django.conf import settings
    from django.db import connection

    django.setup()
    bundle = args.bundle.resolve()
    data = Path(settings.DATA_DIR).resolve()
    need(not bundle.is_relative_to(data), "bundle_must_be_outside_data_directory")
    db = Path(settings.DATABASES["default"]["NAME"])
    need(connection.vendor == "sqlite" and db.is_file() and not db.is_symlink() and db.resolve().parent == data,
         "existing_sqlite_database_required")
    marker = data / ".course-volume"
    if args.action == "export":
        need(not marker.exists() and not marker.is_symlink(), "export_requires_identity_volume")
        return export_bundle(bundle, db.resolve(), source_connection=connection)
    need(marker.is_file() and not marker.is_symlink(), "import_requires_marked_course_volume")
    need(connection.settings_dict["OPTIONS"].get("transaction_mode") == "IMMEDIATE",
         "import_requires_immediate_transactions")
    return import_bundle(bundle, dry_run=args.dry_run)


def main():
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("export", "import"):
        command = commands.add_parser(action)
        command.add_argument("--bundle", required=True, type=Path)
        if action == "import":
            command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(Path.cwd()))
    try:
        receipt = {"success": True, "action": args.action, **dispatch(args)}
    except Exception as error:
        receipt = {"success": False, "action": args.action, "error_type": type(error).__name__}
        if isinstance(error, TransferError):
            receipt["error_code"] = str(error)
        # Diagnostics must not echo DB data, environment values or resolved paths.
        cause = error.__cause__ or error
        if hasattr(cause, "sqlite_errorname"):
            receipt["sqlite_error"] = cause.sqlite_errorname
    print(json.dumps(receipt, ensure_ascii=True), flush=True)
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
