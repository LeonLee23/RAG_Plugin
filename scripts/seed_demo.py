"""Create synthetic PDF/DOCX fixtures; optionally upload and wait for real indexing."""
import argparse
import json
import time
from pathlib import Path

import httpx
import pymupdf
from docx import Document
from docx.shared import Inches
from PIL import Image, ImageDraw


def fixtures(directory):
    directory.mkdir(parents=True, exist_ok=True)
    picture = directory/'project-diagram.png'
    image = Image.new('RGB', (760, 190), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((15, 40, 270, 145), outline='navy', width=3)
    draw.rectangle((440, 40, 745, 145), outline='navy', width=3)
    draw.text((35, 77), 'Phoenix', fill='black', font_size=32)
    draw.text((465, 77), 'StarRiver DB', fill='black', font_size=32)
    draw.line((270, 92, 440, 92), fill='black', width=4)
    draw.polygon([(440, 92), (425, 82), (425, 102)], fill='black')
    draw.text((295, 57), 'uses', fill='black', font_size=24)
    draw.text((20, 155), 'Image-only code: RAG-42', fill='black', font_size=22)
    image.save(picture)
    pdf = directory/'项目职责.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 65), '项目职责说明', fontname='china-s', fontsize=18)
        page.insert_text((60, 105), '张三负责凤凰项目。凤凰项目的英文名称是 Phoenix。', fontname='china-s', fontsize=12)
        page.insert_text((60, 140), '以下架构图中的编号仅存在于图片内。', fontname='china-s', fontsize=12)
        page.insert_image(pymupdf.Rect(60, 180, 540, 300), filename=str(picture))
        doc.save(pdf)
    docx = directory/'数据库配置.docx'
    document = Document()
    document.add_heading('凤凰项目配置', 1)
    document.add_paragraph('凤凰项目使用星河数据库。星河数据库的英文名称是 StarRiver DB。')
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '备份周期'
    table.cell(0, 1).text = '星河数据库每天凌晨 02:00 执行备份。'
    document.add_paragraph('下图为系统示意，图片描述属于派生证据。')
    document.add_picture(str(picture), width=Inches(5.8))
    document.save(docx)
    return [pdf, docx]


def seed(base, files, timeout=900):
    with httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
        response = client.post('/api/knowledge-bases', json={'name': '凤凰项目演示',
                               'description': '自建测试数据：职责、数据库配置与图片；不含真实内部资料。'})
        response.raise_for_status()
        kb_id = response.json()['id']
        prefix = f'/api/knowledge-bases/{kb_id}'
        print(f'kb_id={kb_id}', flush=True)
        ids = []
        for path in files:
            response = client.post(prefix+'/documents', files={'file': (path.name, path.read_bytes())})
            response.raise_for_status()
            ids.append(response.json()['document']['id'])
        deadline = time.monotonic()+timeout
        previous = None
        while time.monotonic() < deadline:
            docs = []
            for doc_id in ids:
                response = client.get(prefix+f'/documents/{doc_id}')
                response.raise_for_status()
                docs.append(response.json())
            state = [(d['filename'], d['status'], d['stage']) for d in docs]
            if state != previous:
                print(json.dumps(state, ensure_ascii=False), flush=True)
                previous = state
            if any(d['status'] == 'failed' for d in docs):
                raise RuntimeError(str([(d['filename'], d['error_code']) for d in docs if d['status'] == 'failed']))
            if all(d['status'] == 'ready' for d in docs):
                return kb_id
            time.sleep(2)
        raise TimeoutError('索引超时；文档保留，可在页面继续查看状态')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('data/demo'))
    parser.add_argument('--upload', action='store_true', help='Calls real model through backend during indexing')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    files = fixtures(args.output)
    print('\n'.join(str(p.resolve()) for p in files), flush=True)
    if args.upload:
        kb_id = seed(args.base_url, files)
        (args.output/'last-run.json').write_text(json.dumps({'kb_id': kb_id}, indent=2), encoding='utf-8')
