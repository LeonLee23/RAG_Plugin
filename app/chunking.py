import re
from uuid import uuid4

from app.config import MAX_CHUNKS
from app.errors import AppError


def fit_end(text, start, prefix, encoder, limit):
    low, high = start, len(text)
    while low < high:
        mid = (low + high + 1)//2
        if encoder.token_count(prefix + text[start:mid]) <= limit:
            low = mid
        else:
            high = mid-1
    if low == start:
        raise AppError("INPUT_TOO_LONG", "标题或单字符无法放入编码窗口", 422)
    if low < len(text):
        boundaries = list(re.finditer(r'[。！？!?\n]', text[start:low]))
        if boundaries and boundaries[-1].end() > (low-start)//2:
            return start + boundaries[-1].end()
    return low


def build_chunks(blocks, encoder):
    """Pack paragraphs while preserving exact source offsets; never silently truncate."""
    chunks, pending = [], []

    def emit(parts):
        if not parts:
            return
        text, spans = '', []
        for block, start, end in parts:
            if text:
                text += '\n'
            offset = len(text)
            text += block.text[start:end]
            spans.append({"block_id": block.block_id, "start": offset, "end": len(text),
                          "source_start": start, "source_end": end, "location": block.location})
        block = parts[0][0]
        prefix = prefix_for(block)
        retrieval = prefix + text
        count = encoder.token_count(retrieval)
        if count > 512:
            raise AppError("INPUT_TOO_LONG", "分块超过模型输入上限", 422)
        chunks.append({"id": uuid4(), "ordinal": len(chunks), "content_type": block.kind,
            "heading_path": block.heading_path, "evidence_text": text, "retrieval_text": retrieval,
            "source_spans": spans, "asset_id": block.asset_id, "token_count": count})
        if len(chunks) > MAX_CHUNKS:
            raise AppError("DOCUMENT_LIMIT", "文档分块超过 1000", 422)

    def prefix_for(block):
        title = ' / '.join(block.heading_path)
        if not title:
            return ''
        if encoder.token_count(title) > 80:
            title = title[:fit_end(title, 0, '', encoder, 80)]
        return title + '\n'

    for block in blocks:
        if not block.text.strip():
            continue
        prefix = prefix_for(block)
        if pending and (block.kind == 'image' or pending[0][0].heading_path != block.heading_path):
            emit(pending)
            pending = []
        start = 0
        while start < len(block.text):
            end = fit_end(block.text, start, prefix, encoder, 450)
            segment = (block, start, end)
            joined = '\n'.join(b.text[s:e] for b, s, e in [*pending, segment])
            if pending and encoder.token_count(prefix + joined) > 450:
                emit(pending)
                pending = []
            pending.append(segment)
            if end < len(block.text) or block.kind == 'image':
                emit(pending)
                pending = []
            if end == len(block.text):
                break
            # Overlap stays in the same source block and carries its actual offsets.
            overlap_start = end
            while overlap_start > start and encoder.token_count(block.text[overlap_start-1:end]) <= 50:
                overlap_start -= 1
            start = max(start+1, overlap_start)
    emit(pending)
    if not chunks:
        raise AppError("NO_EXTRACTABLE_TEXT", "没有可索引内容", 422)
    return chunks
