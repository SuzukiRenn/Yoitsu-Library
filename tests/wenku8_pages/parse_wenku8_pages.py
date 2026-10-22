"""Offline Wenku8 detail/search parser. No HTTP client or browser access."""

import argparse
from datetime import date
import html as entities
import importlib.util
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urljoin, urlsplit

sys.dont_write_bytecode = True
try:
    from lxml import html
except ImportError:
    raise SystemExit("缺少 lxml；本程序不会自动安装依赖。")

HERE = Path(__file__).absolute().parent
LEGACY_PATH = HERE.parent / "wenku8_html" / "parse_wenku8_html.py"
BASE_URL = "https://www.wenku8.net/"
BOOK_PATH = re.compile(r"/book/(\d+)\.htm")
CANDIDATE_FIELDS = (
    "book_id", "title", "author", "publisher_category", "status",
    "last_updated", "word_count", "cover_url", "tags",
    "introduction_excerpt", "detail_url",
)


def load_legacy():
    if not LEGACY_PATH.is_file():
        raise ValueError(f"缺少原解析脚本：{LEGACY_PATH}")
    spec = importlib.util.spec_from_file_location("wenku8_detail_legacy", LEGACY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEGACY = load_legacy()


def clean(text):
    # lxml decodes one entity layer. Some saved excerpts contain &amp;#8231;.
    for _ in range(2):
        decoded = entities.unescape(text)
        if decoded == text:
            break
        text = decoded
    return " ".join(text.split())


def normalized_url(value, base=BASE_URL):
    if not value or not value.strip():
        return None
    url = urljoin(base, value.strip())
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    return url


def book_link(value, base=BASE_URL):
    url = normalized_url(value, base)
    if not url:
        return None
    parts = urlsplit(url)
    match = BOOK_PATH.fullmatch(parts.path)
    if parts.hostname not in ("www.wenku8.net", "wenku8.net") or not match:
        return None
    # Drop fragment/query; these are not part of a book's canonical identifier.
    return int(match[1]), f"https://www.wenku8.net{parts.path}"


def cover_matches(url, book_id):
    return bool(url and re.search(
        rf"/{book_id}/{book_id}s\.(?:jpg|jpeg|png)$", urlsplit(url).path, re.I))


def diagnosis(state="ok", reason=None):
    item = {"state": state}
    if reason:
        item["reason"] = reason
    return item


def word_count(value):
    if value is None:
        return None, diagnosis("absent", "页面没有字数字段")
    value = clean(value)
    exact = re.fullmatch(r"([\d,]+)\s*字?", value)
    if exact and re.fullmatch(r"\d+|\d{1,3}(?:,\d{3})+", exact[1]):
        return int(exact[1].replace(",", "")), diagnosis()
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[Kk万](?:字)?", value):
        return None, diagnosis("inexact", "页面只显示缩略字数；原值在 word_count_raw，不推断精确整数")
    return None, diagnosis("parse_error", "存在字数字段但格式未支持")


def metadata_tables(root):
    tables = {}
    for cell in root.xpath("//td[not(.//td)]"):
        text = clean(cell.text_content())
        labels = {label for label in LEGACY.LABELS.values()
                  if re.match(re.escape(label) + r"\s*[:：]", text)}
        if labels:
            table = cell.xpath("ancestor::table[1]")[0]
            tables.setdefault(table, set()).update(labels)
    # Comments also contain isolated "最后更新" cells. Require distinct fields,
    # while retaining incomplete metadata for explicit field-level diagnostics.
    return [table for table, labels in tables.items() if len(labels) >= 2]


def search_tables(root):
    return [table for table in root.xpath("//table[caption]")
            if (clean(table.find("caption").text_content()) == "搜索结果" or
                re.fullmatch(r"[“\"](.*)[”\"]搜索结果", clean(table.find("caption").text_content())))]


def parse_detail(raw, table, supplied_url):
    ids = set()
    for href in table.xpath(".//a/@href"):
        parts = urlsplit(href)
        if parts.path == "/modules/article/uservote.php":
            values = parse_qs(parts.query).get("id", [])
            ids.update(int(value) for value in values if value.isdigit())
    supplied = book_link(supplied_url) if supplied_url else None
    if supplied_url and not supplied:
        raise ValueError("--source-url 必须是文库8作品详情页 URL")
    if len(ids) != 1:
        raise ValueError("主作品表头的作品编号缺失或不唯一；不从相关推荐推断")
    book_id = next(iter(ids))
    if supplied and supplied[0] != book_id:
        raise ValueError("--source-url 的编号与主作品页面标识不一致")
    source_url = supplied[1] if supplied else urljoin(BASE_URL, f"book/{book_id}.htm")
    old, _, issues = LEGACY.parse_page(raw, source_url)
    result = {"page_type": "detail", "book_id": book_id, **old}
    field_states = {field: diagnosis() if value not in (None, "", [])
                    else diagnosis("parse_error", "详情页必要字段缺失或无法解析")
                    for field, value in old.items()}
    result["word_count_raw"] = old["word_count"]
    result["word_count"], field_states["word_count"] = word_count(old["word_count"])
    field_states["book_id"] = diagnosis()
    if result["title"] and any(word in result["title"] for word in ("推一下", "举报")):
        field_states["title"] = diagnosis("parse_error", "书名混入操作文字")
    result["diagnostics"] = {"fields": field_states, "errors": issues,
                             "identity_evidence": "主作品元数据表中的投票链接 id"}
    return result


def labeled_paragraphs(card):
    paragraphs = card.xpath(".//p")
    labels = {}
    for label in ("作者", "分类", "更新", "字数", "Tags", "简介"):
        found = []
        for p in paragraphs:
            text = clean(p.text_content())
            # Metadata uses slash delimiters; introductory prose must not be split.
            pattern = (r"^简介\s*[:：]\s*(.*)" if label == "简介" else
                       r"(?:^|/)" + re.escape(label) + r"\s*[:：]\s*([^/]*)(?:/|$)")
            match = re.search(pattern, text)
            if match:
                found.append(match[1].strip())
        labels[label] = found
    return labels


def parse_card(card, heading):
    link = book_link(heading.get("href"))
    if not link:
        raise ValueError("搜索结果标题链接不是有效的文库8详情页链接")
    book_id, detail_url = link
    result = {field: ([] if field == "tags" else None) for field in CANDIDATE_FIELDS}
    result.update(book_id=book_id, detail_url=detail_url)
    states = {field: diagnosis("absent", "当前候选卡片未提供此字段") for field in result}
    states.update(book_id=diagnosis(), detail_url=diagnosis())
    result["title"] = clean(heading.get("title") or heading.text_content()) or None
    states["title"] = diagnosis() if result["title"] else diagnosis("parse_error", "标题节点为空")
    if result["title"] and not heading.get("title"):
        states["title"] = diagnosis("unverified_full_title", "缺少完整 title 属性，使用可见文本，可能截断")
    labels = labeled_paragraphs(card)
    mapping = {"author": "作者", "publisher_category": "分类", "last_updated": "更新",
               "word_count": "字数", "tags": "Tags", "introduction_excerpt": "简介"}
    for field, label in mapping.items():
        values = labels[label]
        if not values:
            # Label still present means unsupported syntax, not genuine absence.
            if any(re.search(re.escape(label) + r"\s*[:：]", clean(p.text_content()))
                   for p in card.xpath(".//p")):
                states[field] = diagnosis("parse_error", "字段标签存在，但格式不受支持")
            continue
        if len(values) != 1 or not values[0]:
            states[field] = diagnosis("parse_error", "字段为空或重复")
            continue
        value = values[0]
        if field == "word_count":
            result["word_count_raw"] = value
            result[field], states[field] = word_count(value)
        else:
            result[field] = list(dict.fromkeys(value.split())) if field == "tags" else value
            states[field] = diagnosis()
    result.setdefault("word_count_raw", None)
    # The status is an unlabelled token following the 字数 token in the update paragraph.
    updates = [clean(p.text_content()) for p in card.xpath(".//p")
               if re.match(r"更新\s*[:：]", clean(p.text_content()))]
    if updates:
        matches = [re.search(r"/字数\s*[:：][^/]*/([^/]+)", text) for text in updates]
        values = [m[1].strip() for m in matches if m and m[1].strip()]
        if len(values) == 1:
            result["status"] = values[0]
            states["status"] = diagnosis()
        else:
            states["status"] = diagnosis("parse_error", "更新行存在，但未找到唯一状态")
    if result["last_updated"]:
        try:
            date.fromisoformat(result["last_updated"])
        except ValueError:
            states["last_updated"] = diagnosis("parse_error", "日期格式无效")
    cover_nodes = [img for img in card.xpath(".//a/img")
                   if book_link(img.getparent().get("href")) == link]
    urls = {normalized_url(img.get("src")) for img in cover_nodes}
    valid = {url for url in urls if cover_matches(url, book_id)}
    if len(valid) == 1 and len(urls) == 1:
        result["cover_url"] = next(iter(valid))
        states["cover_url"] = diagnosis()
    elif cover_nodes:
        states["cover_url"] = diagnosis("parse_error", "图片存在，但编号不符、URL 无效或存在歧义")
    result["diagnostics"] = {"fields": states}
    return result


def parse_search(table, supplied_keyword):
    caption = clean(table.find("caption").text_content())
    match = re.fullmatch(r"[“\"](.*)[”\"]搜索结果", caption)
    keyword = clean(match[1]) if match and match[1].strip() else None
    if supplied_keyword is not None:
        if keyword and keyword != supplied_keyword:
            raise ValueError("--keyword 与搜索结果 caption 中的关键词不一致")
        keyword = supplied_keyword
    errors, results, seen = [], [], {}
    duplicates = 0
    headings = table.xpath('.//b/a[@href]')
    for heading in headings:
        if not book_link(heading.get("href")):
            continue
        # The outer card has a child DIV for metadata and a sibling DIV for cover.
        # Selecting the inner metadata DIV would silently omit all cover images.
        cards = [node for node in heading.iterancestors("div")
                 if node in table.iterdescendants() and node.xpath("./div[.//b/a]")
                 and node.xpath(".//p")]
        if not cards:
            errors.append("标题链接存在，但未找到候选卡片结构")
            continue
        item = parse_card(cards[0], heading)
        book_id = item["book_id"]
        if book_id in seen:
            duplicates += 1
            if item != seen[book_id]:
                errors.append(f"作品 {book_id} 的重复卡片内容冲突")
            continue
        seen[book_id] = item
        results.append(item)
    linked_ids = {value[0] for href in table.xpath(".//a/@href")
                  for value in [book_link(href)] if value}
    if linked_ids != set(seen):
        errors.append("结果表中存在未能解析为卡片的作品链接，候选可能不完整")
    return {"page_type": "search_results", "keyword": keyword, "results": results,
            "result_count": len(results), "diagnostics": {
                "fields": {"keyword": diagnosis() if keyword else diagnosis("absent", "HTML 中没有可靠关键词；可传 --keyword")},
                "errors": errors, "duplicate_cards_removed": duplicates,
                "scope": "仅 caption 标识的搜索结果表；按作品编号去重；数量仅代表本页"}}


def parse_page(raw, *, keyword=None, source_url=None):
    text, encoding = LEGACY.decode_html(raw)
    root = html.fromstring(text, parser=html.HTMLParser(no_network=True, remove_comments=True))
    detail, search = metadata_tables(root), search_tables(root)
    if len(detail) == 1:
        result = parse_detail(raw, detail[0], source_url)
    elif not detail and len(search) == 1:
        result = parse_search(search[0], keyword)
    else:
        raise ValueError("无法唯一识别详情页或搜索结果表；不将任意作品链接当搜索列表")
    result["diagnostics"]["encoding"] = encoding
    return result


def has_errors(result):
    objects = [result, *result.get("results", [])]
    return any(obj.get("diagnostics", {}).get("errors") or any(
        state["state"] == "parse_error" for state in obj.get("diagnostics", {}).get("fields", {}).values())
        for obj in objects)


def render_report(records):
    lines = ["# 文库8统一解析离线报告", "", "仅处理指定本地 HTML；无网络请求、浏览器操作或封面下载。", "",
             "字段状态：ok=成功；absent=页面无字段；parse_error=存在但解析失败；"
             "inexact=仅有缩略值；unverified_full_title=无法核实完整书名。", ""]
    for path, result in records:
        lines += [f"## {path.name}", "", f"类型：{result['page_type']}；编码：{result['diagnostics']['encoding']}。", ""]
        objects = result.get("results", [result])
        if result["page_type"] == "search_results":
            lines += [f"关键词：{result['keyword']}；实际候选数：{result['result_count']}；"
                      f"编号：{', '.join(str(item['book_id']) for item in objects)}。", ""]
        for obj in objects:
            lines += [f"作品 {obj['book_id']}：{obj['title']}", ""]
            for field, state in obj["diagnostics"]["fields"].items():
                lines.append(f"- {field}: {state['state']}" + (f"；{state['reason']}" if state.get("reason") else ""))
            lines.append("")
        errors = result["diagnostics"].get("errors", [])
        lines += ["解析错误：" + ("；".join(errors) if errors else "无"), ""]
    lines += ["## 范围及后续", "", "详情页复用原脚本；搜索页仅使用带搜索结果 caption 的表和候选卡片。"
              "标题优先 title 属性，封面必须来自同一作品链接且图片编号一致；简介只取主作品/卡片正文。", "",
              "缩略 K/万 字数原值保存在 word_count_raw，word_count 为 null 且状态为 inexact；"
              "不能还原精确字数，最小补充方案是在后续独立取得详情页后再更新精确值，本轮不请求详情页。", "",
              "本地解析兼容性不等于在线访问可用。在线阶段仍需可控制的已登录浏览器及实际搜索/跳转记录；"
              "零结果、验证码页、分页和不同版式尚需新增真实样本验证。", ""]
    return "\n".join(lines)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--keyword", help="仅在 HTML 无可靠关键词时补充；与已有关键词冲突则报错")
    parser.add_argument("--source-url", help="仅允许单文件使用；与详情页作品编号交叉核对")
    parser.add_argument("--output-dir", type=Path, default=HERE / "output")
    args = parser.parse_args()
    missing = [str(path) for path in args.inputs if not path.is_file()]
    if missing:
        parser.error("缺少必要 HTML：" + "、".join(missing))
    if args.source_url and len(args.inputs) != 1:
        parser.error("--source-url 只允许单文件使用")
    if len({path.stem for path in args.inputs}) != len(args.inputs):
        parser.error("输入文件同名，输出可能互相覆盖")
    targets = [args.output_dir / (path.stem + ".json") for path in args.inputs]
    targets.append(args.output_dir / "test_report.md")
    if any(target.absolute() == path.absolute() for target in targets for path in args.inputs):
        parser.error("输出不可覆盖输入")
    records = []
    for path in args.inputs:
        try:
            result = parse_page(path.read_bytes(), keyword=args.keyword, source_url=args.source_url)
        except (ValueError, LookupError) as exc:
            parser.exit(1, f"{path.name}：{exc}\n")
        records.append((path, result))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for path, result in records:
        (args.output_dir / (path.stem + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{path.name}: {result['page_type']}，"
              + (f"候选 {result['result_count']} 本" if 'results' in result else f"作品 {result['book_id']}")
              + ("，存在解析错误" if has_errors(result) else "，无解析错误"))
    (args.output_dir / "test_report.md").write_text(render_report(records), encoding="utf-8")
    return 1 if any(has_errors(result) for _, result in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
