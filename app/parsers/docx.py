import re

from docx import Document
from lxml import etree

from app.config import MAX_IMAGES
from app.errors import AppError
from app.parsers import Block, save_image

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'v': 'urn:schemas-microsoft-com:vml',
}


def tag(prefix, name):
    return f"{{{NS[prefix]}}}{name}"


def owner_paragraph(element):
    return next(element.iterancestors(tag('w', 'p')), None)


def parse_docx(path, output_dir):
    blocks, assets = [], {}
    try:
        doc = Document(path)
        parts = [p for p in doc.part.package.parts
                 if re.fullmatch(r'/word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml', str(p.partname))]
        parts.sort(key=lambda p: (str(p.partname) != '/word/document.xml', str(p.partname)))
        for part in parts:
            root = etree.fromstring(part.blob, parser=etree.XMLParser(resolve_entities=False, no_network=True))
            tables = {t: i+1 for i, t in enumerate(root.iter(tag('w', 'tbl')))}
            heading = []
            for idx, paragraph in enumerate(root.iter(tag('w', 'p')), 1):
                text = ''.join(t.text or '' for t in paragraph.iter(tag('w', 't'))
                               if owner_paragraph(t) is paragraph).strip()
                style = paragraph.find('./w:pPr/w:pStyle', NS)
                value = style.get(tag('w', 'val'), '') if style is not None else ''
                match = re.search(r'(?:Heading|标题)\s*([1-9])', value, re.I)
                if match and text:
                    level = int(match[1])
                    heading = heading[:level-1] + [text]
                loc = {"kind": "docx", "part": str(part.partname).lstrip('/'),
                       "paragraph": idx, "heading_path": list(heading)}
                parents = list(paragraph.iterancestors())
                table_path = [tables[t] for t in reversed(parents) if t.tag == tag('w', 'tbl')]
                if table_path:
                    loc['table_path'] = table_path
                    for element, field, name in ((tag('w', 'tr'), 'row', 'tr'), (tag('w', 'tc'), 'cell', 'tc')):
                        parent = next((a for a in parents if a.tag == element), None)
                        if parent is not None:
                            loc[field] = list(parent.getparent().iterchildren(tag('w', name))).index(parent)+1
                block_id = f"{loc['part']}:p{idx}"
                if text:
                    blocks.append(Block(block_id, 'text', text, loc, list(heading)))
                for node in paragraph.iter():
                    if owner_paragraph(node) is not paragraph:
                        continue
                    relationship = None
                    if node.tag == tag('a', 'blip'):
                        relationship = node.get(tag('r', 'embed'))
                    elif node.tag == tag('v', 'imagedata'):
                        relationship = node.get(tag('r', 'id'))
                    if not relationship:
                        continue
                    if len(assets) >= MAX_IMAGES:
                        raise AppError("DOCUMENT_LIMIT", "文档图片超过 100 张", 422)
                    image_part = part.related_parts[relationship]
                    asset_id = f"img-{len(assets)+1:04d}"
                    target = save_image(image_part.blob, output_dir, asset_id)
                    image_loc = {**loc, "image": asset_id}
                    assets[asset_id] = {"path": str(target), "location": image_loc}
                    blocks.append(Block(f"{block_id}:{asset_id}", 'image', '', image_loc,
                                        list(heading), asset_id))
    except AppError:
        raise
    except Exception as exc:
        raise AppError("PARSE_FAILED", "DOCX 解析失败或包含不支持的图片", 422) from exc
    if not blocks:
        raise AppError("NO_EXTRACTABLE_TEXT", "文档没有可处理内容", 422)
    return blocks, assets
