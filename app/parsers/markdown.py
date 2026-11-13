"""CommonMark tokens supply headings/images; source text is never rewritten."""
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from app.config import MAX_IMAGES, MAX_TEXT
from app.errors import AppError
from app.parsers import Block, save_image
from app.storage import inside


def markdown_tokens(text):
    if len(text) > MAX_TEXT:
        raise AppError('DOCUMENT_LIMIT', '正文超过 200 万字符')
    return MarkdownIt('commonmark').parse(text.removeprefix('\ufeff'))


def image_references(text):
    refs = []
    for token in markdown_tokens(text):
        if token.type in ('html_block', 'html_inline') and '<img' in token.content.lower():
            raise AppError('FORMAT_UNSUPPORTED', 'MD 图片请使用 Markdown 图片语法')
        for child in token.children or []:
            if child.type == 'html_inline' and '<img' in child.content.lower():
                raise AppError('FORMAT_UNSUPPORTED', 'MD 图片请使用 Markdown 图片语法')
            if child.type != 'image':
                continue
            src = child.attrGet('src') or ''
            parsed = urlsplit(src)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                raise AppError('FORMAT_UNSUPPORTED', 'MD 仅支持同目录树内相对路径图片，不下载外链')
            rel = unquote(parsed.path)
            # Validate lexical paths before the file is read or copied.
            inside('.', rel)
            refs.append((rel, (token.map or [0, 1])[0] + 1))
    if len(refs) > MAX_IMAGES:
        raise AppError('DOCUMENT_LIMIT', '文档图片超过 100 次出现')
    return refs


def parse_markdown(path, output_dir):
    try:
        text = path.read_bytes().decode('utf-8')
    except UnicodeError as exc:
        raise AppError('PARSE_FAILED', 'Markdown 必须为 UTF-8') from exc
    if not text.strip():
        raise AppError('NO_EXTRACTABLE_TEXT', 'Markdown 为空')
    lines = text.splitlines(keepends=True)
    tokens = markdown_tokens(text)
    headings = []
    for i, token in enumerate(tokens):
        if token.type == 'heading_open':
            title = tokens[i + 1].content
            headings.append((token.map[0], int(token.tag[1:]), title))
    sections = [(0, None, '')] if not headings or headings[0][0] else []
    sections += headings
    blocks, assets = [], {}
    for index, (start, level, title) in enumerate(sections):
        end = sections[index + 1][0] if index + 1 < len(sections) else len(lines)
        location = {'kind': 'md', 'line_start': start + 1, 'line_end': end,
                    'heading_path': [title] if title else []}
        blocks.append(Block(f'l{start+1}', 'text', ''.join(lines[start:end]), location,
                            [title] if title else [], heading_level=level))
    for src, line in image_references(text):
        asset_id = f'img-{len(assets)+1:04d}'
        source = inside(path.parent, src)
        image = save_image(source.read_bytes(), output_dir, asset_id)
        location = {'kind': 'md', 'line_start': line, 'line_end': line, 'image': asset_id}
        assets[asset_id] = {'path': str(image), 'location': location}
    return blocks, assets
