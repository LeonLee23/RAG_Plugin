import statistics

import pymupdf

from app.config import MAX_IMAGES, MAX_PAGES
from app.errors import AppError
from app.parsers import Block, save_image


def parse_pdf(path, output_dir):
    blocks, assets = [], {}
    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                raise AppError("ENCRYPTED_DOCUMENT", "暂不支持加密 PDF", 422)
            if len(doc) > MAX_PAGES:
                raise AppError("DOCUMENT_LIMIT", "PDF 页数超过 200 页", 422)
            heading = []
            for page_index, page in enumerate(doc):
                items = page.get_text('dict', sort=True)['blocks']
                sizes = [span['size'] for b in items if b['type'] == 0
                         for line in b['lines'] for span in line['spans'] if span['text'].strip()]
                median = statistics.median(sizes) if sizes else 12
                for n, item in enumerate(items):
                    block_id = f"p{page_index+1:04d}-b{n+1:04d}"
                    location = {"kind": "pdf", "page": page_index+1, "block_id": block_id,
                                "bbox": list(item['bbox'])}
                    if item['type'] == 0:
                        text = '\n'.join(''.join(s['text'] for s in line['spans'])
                                         for line in item['lines']).strip()
                        if not text:
                            continue
                        max_size = max(s['size'] for line in item['lines'] for s in line['spans'])
                        if max_size > median * 1.3 and len(text) < 80:
                            heading = [text]
                        blocks.append(Block(block_id, 'text', text, location, list(heading)))
                    elif item['type'] == 1:
                        asset_id = f"img-{len(assets)+1:04d}"
                        if len(assets) >= MAX_IMAGES:
                            raise AppError("DOCUMENT_LIMIT", "文档图片超过 100 张", 422)
                        image_path = save_image(item['image'], output_dir, asset_id)
                        assets[asset_id] = {"path": str(image_path), "location": location}
                        blocks.append(Block(block_id, 'image', '', location, list(heading), asset_id))
    except AppError:
        raise
    except Exception as exc:
        raise AppError("PARSE_FAILED", "PDF 解析失败", 422) from exc
    if not any(b.kind == 'text' and b.text.strip() for b in blocks):
        raise AppError("NO_EXTRACTABLE_TEXT", "PDF 无可提取文本，不支持扫描件 OCR", 422)
    return blocks, assets
