import io
import sys
import time
from uuid import uuid4

import pytest
from docx import Document
from PIL import Image
from pydantic import ValidationError

from app.chunking import build_chunks
from app.errors import AppError
from app.knowledge import validate_upload
from app.models import run_bounded
from app.parsers import Block, parse_document
from app.retrieval.graph import mentions
from app.retrieval.search import merge_rankings
from app.schemas import SearchRequest


def test_request_validation():
    for data in ({'query': '  '}, {'query': 'a', 'mode': 'bogus'}, {'query': 'a', 'top_k': 21},
                 {'query': 'a', 'mode': 'vector', 'allow_partial': True}):
        with pytest.raises(ValidationError):
            SearchRequest(**data)


def test_file_validation():
    with pytest.raises(AppError):
        validate_upload('fake.pdf', b'not PDF')
    with pytest.raises(AppError):
        validate_upload('old.doc', b'1234')


def test_chunk_offsets_and_long_paragraph(encoder):
    text = ('张三负责凤凰项目。' * 130)
    block = Block('p1', 'text', text, {'kind': 'pdf', 'page': 1}, ['标题'])
    chunks = build_chunks([block], encoder)
    covered = set()
    for chunk in chunks:
        assert chunk['token_count'] <= 512
        for span in chunk['source_spans']:
            assert chunk['evidence_text'][span['start']:span['end']] == text[span['source_start']:span['source_end']]
            covered.update(range(span['source_start'], span['source_end']))
    assert covered == set(range(len(text)))


def test_docx_body_table_and_images(tmp_path):
    image = io.BytesIO()
    Image.new('RGB', (10, 10), 'red').save(image, 'PNG')
    document = Document()
    document.add_heading('项目', 1)
    document.add_paragraph('凤凰项目使用星河数据库。')
    document.add_table(rows=1, cols=1).cell(0, 0).text = '表格证据'
    document.add_picture(io.BytesIO(image.getvalue()))
    # Convert a second drawing to an anchored drawing; extraction must not rely on inline_shapes.
    shape = document.add_picture(io.BytesIO(image.getvalue()))
    from docx.oxml.ns import qn
    shape._inline.tag = qn('wp:anchor')
    path = tmp_path/'sample.docx'
    document.save(path)
    blocks, assets = parse_document(path, 'docx', tmp_path/'images')
    assert len(assets) == 2
    assert any(b.text == '表格证据' and b.location['table_path'] for b in blocks)
    assert len([b for b in blocks if b.kind == 'image']) == 2
    assert all(b.location.get('paragraph') for b in blocks)


def test_pdf_image_and_page(tmp_path):
    import pymupdf
    image = io.BytesIO()
    Image.new('RGB', (10, 10), 'blue').save(image, 'PNG')
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), 'Project Phoenix')
    page.insert_image(pymupdf.Rect(72, 100, 150, 180), stream=image.getvalue())
    path = tmp_path/'sample.pdf'
    doc.save(path)
    doc.close()
    blocks, assets = parse_document(path, 'pdf', tmp_path/'images')
    assert len(assets) == 1
    assert all(b.location['page'] == 1 for b in blocks)


def test_rrf_dedup_and_name_boundaries():
    a, b = uuid4(), uuid4()
    merged = merge_rankings({'vector': [{'chunk_id': a, 'score': 0.9}, {'chunk_id': a, 'score': 0.8}],
                            'keyword': [{'chunk_id': b, 'score': 1}, {'chunk_id': a, 'score': 0.5}]})
    assert merged[0]['chunk_id'] == a
    assert merged[0]['rrf'] == pytest.approx(1/61 + 1/62)
    assert mentions('A1 uses database'.casefold(), 'a1')
    assert not mentions('A100 uses database'.casefold(), 'a1')


def test_subprocess_timeout_and_output_limit(tmp_path):
    started = time.monotonic()
    with pytest.raises(AppError, match='超时'):
        run_bounded([sys.executable, '-c', 'import time;time.sleep(30)'], input_bytes=b'',
                    cwd=tmp_path, timeout=.15)
    assert time.monotonic()-started < 6
    with pytest.raises(AppError, match='超过限制'):
        run_bounded([sys.executable, '-c', 'print("x"*1500000)'], input_bytes=b'', cwd=tmp_path, timeout=5)
