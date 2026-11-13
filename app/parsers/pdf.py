import pymupdf

from app.config import MAX_IMAGES, MAX_PAGES
from app.errors import AppError
from app.parsers import Block, save_image


def parse_pdf(path, output_dir):
    blocks, assets = [], {}
    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                raise AppError('FORMAT_UNSUPPORTED', '暂不支持加密 PDF')
            if len(doc) > MAX_PAGES:
                raise AppError('DOCUMENT_LIMIT', 'PDF 页数超过 200 页')
            toc = [row for row in doc.get_toc() if 1 <= row[2] <= len(doc)]
            # Only monotonic, page-addressable bookmarks are structural evidence.
            if any(toc[i][2] > toc[i+1][2] for i in range(len(toc)-1)):
                toc = []
            for page_index, page in enumerate(doc):
                page_no = page_index + 1
                headings = [row for row in toc if row[2] == page_no]
                for level, title, _ in headings:
                    blocks.append(Block(f'toc-{len(blocks)}', 'section', title,
                                        {'kind': 'pdf', 'page': page_no}, heading_level=level,
                                        structure_quality='bookmarks'))
                if not toc or (page_index == 0 and not headings):
                    blocks.append(Block(f'page-{page_no}', 'section', f'第 {page_no} 页',
                                        {'kind': 'pdf', 'page': page_no}, heading_level=1,
                                        structure_quality='pages'))
                for n, item in enumerate(page.get_text('dict', sort=True)['blocks']):
                    block_id = f'p{page_no:04d}-b{n+1:04d}'
                    location = {'kind': 'pdf', 'page': page_no, 'block_id': block_id,
                                'bbox': list(item['bbox'])}
                    if item['type'] == 0:
                        text = '\n'.join(''.join(s['text'] for s in line['spans'])
                                         for line in item['lines']).strip()
                        if text:
                            blocks.append(Block(block_id, 'text', text, location))
                    elif item['type'] == 1:
                        if len(assets) >= MAX_IMAGES:
                            raise AppError('DOCUMENT_LIMIT', '文档图片超过 100 次出现')
                        asset_id = f'img-{len(assets)+1:04d}'
                        image_path = save_image(item['image'], output_dir, asset_id)
                        assets[asset_id] = {'path': str(image_path), 'location': location}
                        blocks.append(Block(block_id, 'image', '', location, asset_id=asset_id))
    except AppError:
        raise
    except Exception as exc:
        raise AppError('PARSE_FAILED', 'PDF 解析失败') from exc
    if not any(b.kind == 'text' and b.text.strip() for b in blocks):
        raise AppError('NO_EXTRACTABLE_TEXT', 'PDF 无可提取文本，不支持扫描件 OCR')
    return blocks, assets
