"""Parse one saved Wenku8 detail page locally. Never fetch URLs or images."""

import argparse
import codecs
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit

try:
    from lxml import html
except ImportError:
    raise SystemExit("缺少 lxml；未自动安装任何依赖。")


LABELS = {
    "author": "小说作者",
    "publisher_category": "文库分类",
    "status": "文章状态",
    "last_updated": "最后更新",
    "word_count": "全文长度",
}
FIELDS = ("title", *LABELS, "cover_url", "tags", "introduction", "source_url")


def compact(text):
    return " ".join(text.split())


def value_after_label(node, label):
    match = re.fullmatch(re.escape(label) + r"\s*[:：]\s*(.*)",
                         compact(node.text_content()))
    return match.group(1).strip() if match else None


def decode_html(raw):
    # Only the document's actual charset declaration, not script strings such as big5.
    declaration = re.search(
        br'<meta\b[^>]*charset\s*=\s*["\x27]?\s*([A-Za-z0-9_-]+)',
        raw, re.IGNORECASE,
    )
    if not declaration:
        raise ValueError("未找到 meta charset，拒绝猜测编码")
    encoding = codecs.lookup(declaration.group(1).decode("ascii")).name
    return raw.decode(encoding, errors="strict"), encoding


def plain_paragraphs(node):
    parts = []

    def visit(element):
        if element.tag in ("script", "style"):
            return
        if element.tag == "br":
            parts.append("\n")
            return
        if element.text:
            parts.append(re.sub(r"\s+", " ", element.text))
        for child in element:
            visit(child)
            if child.tail:
                parts.append(re.sub(r"\s+", " ", child.tail))
        if element.tag in ("p", "div"):
            parts.append("\n\n")

    visit(node)
    # Only HTML breaks create paragraphs; source indentation is ordinary whitespace.
    text = "".join(parts)
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def parse_page(raw, source_url):
    text, encoding = decode_html(raw)
    root = html.fromstring(text, parser=html.HTMLParser(no_network=True, remove_comments=True))
    result = {field: ([] if field == "tags" else None) for field in FIELDS}
    result["source_url"] = source_url
    issues = []

    def unique(nodes, description):
        if len(nodes) != 1:
            issues.append(f"{description}：预期 1 个节点，实际 {len(nodes)} 个")
            return None
        return nodes[0]

    category_cells = [cell for cell in root.xpath("//td")
                      if value_after_label(cell, "文库分类") is not None
                      and not cell.xpath(".//td")]
    category_cell = unique(category_cells, "元数据定位单元格")
    metadata = category_cell.xpath("ancestor::table[1]")[0] if category_cell is not None else None
    if metadata is not None:
        row = category_cell.getparent()
        for field, label in LABELS.items():
            values = [value_after_label(cell, label) for cell in row.xpath("./td")]
            values = [value for value in values if value is not None]
            if len(values) == 1 and values[0]:
                result[field] = values[0]
            else:
                issues.append(f"{field}：标签值缺失或不唯一")

        # Header <b> contains only the title; voting/report links are siblings.
        rows = metadata.xpath("./tr | ./tbody/tr")
        titles = rows[0].xpath("./td/table//span/b") if rows else []
        title = unique(titles, "title 书名节点")
        if title is not None:
            result["title"] = compact(title.text_content()) or None

        # Restrict to the adjacent detail table, never recommendations elsewhere.
        detail_tables = metadata.xpath("following-sibling::table[1]")
        detail = unique(detail_tables, "作品详情表格")
        if detail is not None:
            intro_labels = [node for node in detail.xpath(".//td/span")
                            if compact(node.text_content()) in ("内容简介：", "内容简介:")]
            intro_label = unique(intro_labels, "introduction 标签")
            if intro_label is not None:
                intro = unique(intro_label.xpath("following-sibling::span[1]"), "introduction 正文")
                if intro is not None:
                    result["introduction"] = plain_paragraphs(intro) or None
                cell = intro_label.getparent()
                tag_nodes = [node for node in cell.xpath("./span/b")
                             if value_after_label(node, "作品Tags") is not None]
                tags = unique(tag_nodes, "tags 标签节点")
                if tags is not None:
                    result["tags"] = list(dict.fromkeys(
                        value_after_label(tags, "作品Tags").split()))

                book = re.fullmatch(r"/book/(\d+)\.htm", urlsplit(source_url).path)
                images = cell.xpath("preceding-sibling::td[1]/img")
                matching = []
                for img in images:
                    url = urljoin(source_url, img.get("src", ""))
                    parsed = urlsplit(url)
                    if (book and parsed.scheme in ("http", "https") and parsed.hostname
                            and re.search(r"/" + book[1] + r"/" + book[1] + r"s\.(?:jpg|jpeg|png)$",
                                          parsed.path, re.IGNORECASE)):
                        matching.append(url)
                if len(matching) == 1:
                    result["cover_url"] = matching[0]
                else:
                    issues.append(f"cover_url：同一详情行中匹配作品编号的封面数量为 {len(matching)}")

    if result["last_updated"]:
        try:
            date.fromisoformat(result["last_updated"])
        except ValueError:
            issues.append("last_updated：日期格式无效")
    if result["word_count"] and not re.fullmatch(r"\d+字", result["word_count"]):
        issues.append("word_count：不是预期的数字加“字”格式，已保留原文")
    if result["title"] and "推一下" in result["title"]:
        issues.append("title：混入页面操作文字")
    missing = [field for field, value in result.items() if value is None or value == [] or value == ""]
    issues.extend(f"{field}：未成功提取" for field in missing)
    return result, encoding, issues


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="唯一要读取的本地 HTML 文件")
    parser.add_argument("--source-url", default="https://www.wenku8.net/book/4151.htm",
                        help="仅作为来源记录、相对链接基址和作品编号；不会访问")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).absolute().parent)
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error("输入不是本地文件")
    if not re.fullmatch(r"/book/\d+\.htm", urlsplit(args.source_url).path):
        parser.error("source-url 必须包含 /book/数字.htm 路径")
    if not args.output_dir.is_dir():
        parser.error("输出目录必须已存在")
    try:
        result, encoding, issues = parse_page(args.input.read_bytes(), args.source_url)
    except (ValueError, LookupError) as exc:
        print(f"解析失败：{exc}", file=sys.stderr)
        return 1
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    output = args.output_dir / "result.json"
    report = args.output_dir / "test_report.md"
    if args.input.absolute() in (output.absolute(), report.absolute()):
        parser.error("输出不得覆盖输入文件")
    output.write_text(payload + "\n", encoding="utf-8")
    checks = [f"- {field}: {'缺失' if result[field] is None or result[field] == [] or result[field] == '' else '成功'}"
              for field in FIELDS]
    summary = (
        "# 本地 HTML 解析检查报告\n\n"
        f"输入：{args.input.absolute()}\n\n编码：{encoding}（严格解码）\n\n"
        "仅读取指定 HTML；未请求网络、执行脚本、下载图片或读取关联资源。\n"
        "仅输出作品字段；不提取登录、账号或评论信息。\n\n"
        + "\n".join(checks) + "\n\n检查结论："
        + ("通过" if not issues else "存在问题") + "\n"
        + "\n".join(f"- {issue}" for issue in issues)
        + "\n\n结构依赖：元数据同行的中文标签；元数据表头 span/b；相邻详情表；"
        "作品Tags 的 span/b；内容简介标签后的 span；简介左侧单元格中的封面及作品编号路径。\n\n"
        "word_count 保留页面原文和单位，日期保留原文；source_url 来自命令行已知地址，非页面推断。\n\n"
        "可对同版式其他作品继续离线测试，须显式指定 --source-url；跨版式兼容性尚未验证。"
        "缺失字段输出 null（tags 为 []），终端明确报告并以退出码 1 结束。\n\n"
        "未来在线搜索需另行授权；需核实访问规则、搜索入口与编码、书名及作者消歧、"
        "登录或验证码限制、请求频率与缓存、异常页面识别及版式变化。本次未在线核实。\n"
    )
    report.write_text(summary, encoding="utf-8")
    print(payload)
    print("\n" + summary)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
