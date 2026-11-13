"""Deterministic candidates and version-bound evidence; no answer generation."""
import base64
import io
import json
from collections import Counter

from PIL import Image

from app.config import IMAGE_EDGE, MAX_IMAGE_BYTES
from app.errors import AppError
from app.indexing import bundle
from app.storage import (
    check_revision,
    cursor_offset,
    digest,
    fingerprint,
    finish_read,
    inside,
    make_cursor,
    mounted,
    page,
)
from app.text import normalize, terms


def document(value, doc_id, doc_revision=None):
    doc = value['documents'].get(doc_id)
    if not doc:
        raise AppError('DOCUMENT_NOT_FOUND', '文档不存在或不属于该库')
    if doc_revision is not None and doc['doc_revision'] != doc_revision:
        raise AppError('STALE_REVISION', '文档版本已变化')
    return doc


def response(root, value, result):
    finish_read(root, value)
    warnings = [{'code': 'UPDATE_PENDING', 'doc_id': t['doc_id'], 'task_id': key, 'state': t['state']}
                for key, t in value['tasks'].items()
                if t['doc_id'] in value['documents'] and t['state'] not in ('published', 'cancelled')]
    return {'kb_id': value['kb_id'], 'kb_revision': value['kb_revision'],
            'warnings': warnings[:20], 'warnings_truncated': len(warnings) > 20, **result}


def node_by_id(tree, node_id):
    node = next((n for n in tree['nodes'] if n['node_id'] == node_id), None)
    if node is None:
        raise AppError('INVALID_ARGUMENT', 'node_id 不属于指定文档版本')
    return node


def text_page(content, node, locations, scope, revision, cursor=None):
    offset = cursor_offset(cursor, scope, revision)
    start = node['start'] + offset
    if start > node['end']:
        raise AppError('INVALID_ARGUMENT', '分页超出节点范围')
    end = min(start + 12000, node['end'])
    selected = [m for m in locations if m['start'] < end and m['end'] > start]
    if len(selected) > 100:
        end = selected[99]['end']
        selected = selected[:100]
    locs = []
    for mapping in selected:
        a, b = max(start, mapping['start']), min(end, mapping['end'])
        loc = dict(mapping['location'])
        if loc['kind'] == 'md':
            first = loc['line_start'] + content[mapping['start']:a].count('\n')
            loc.update(line_start=first, line_end=first + content[a:b].rstrip('\n').count('\n'))
        locs.append({'start': a, 'end': b, 'location': loc})
    return {'text': content[start:end], 'start': start, 'end': end, 'locations': locs,
            'truncated': end < node['end'],
            'next_cursor': make_cursor(scope, revision, end - node['start']) if end < node['end'] else None}


def image_result(folder, asset):
    path = inside(folder, asset['path'])
    data = path.read_bytes()
    if digest(data) != asset['sha256']:
        raise AppError('INDEX_STALE', '图片哈希变化')
    with Image.open(io.BytesIO(data)) as image:
        image.thumbnail((IMAGE_EDGE, IMAGE_EDGE))
        output = io.BytesIO()
        image.convert('RGB').save(output, format='PNG')
        data = output.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError('DOCUMENT_LIMIT', '缩放图像超过传输上限')
    return {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(data).decode()}


def search_knowledge_base(settings, kb_id, query, match='terms', doc_ids=None, limit=20,
                          cursor=None, expected_kb_revision=None):
    if not query.strip() or len(query) > 2000 or match not in ('terms', 'exact'):
        raise AppError('INVALID_ARGUMENT', 'query 或 match 无效')
    if doc_ids is not None and (not 1 <= len(doc_ids) <= 100 or len(set(doc_ids)) != len(doc_ids)):
        raise AppError('INVALID_ARGUMENT', 'doc_ids 必须为 1..100 个不重复 ID')
    root, value = mounted(settings, kb_id)
    check_revision(value, expected_kb_revision)
    ids = doc_ids if doc_ids is not None else list(value['documents'])
    query_terms = terms(query, query=True) if match == 'terms' else []
    results = []
    for doc_id in ids:
        doc = document(value, doc_id)
        content, tree, locations, derived = bundle(root, doc)
        candidates = [(n['node_id'], 'original', content[n['start']:n['end']], n['title'], None)
                      for n in tree['nodes'] if n['end'] > n['start']]
        for key, asset in tree['assets'].items():
            item = derived['items'][key]
            body = item['text'] + '\n' + '\n'.join(item['visible_text'])
            candidates.append((asset['node_id'], 'image_description', body, '', key))
        for node_id, kind, body, title, asset_id in candidates:
            if match == 'exact':
                score = body.count(query) + 3 * title.count(query)
                position = body.find(query)
            else:
                counts = Counter(terms(body))
                title_counts = Counter(terms(title))
                score = sum(counts[t] + 3 * title_counts[t] for t in query_terms)
                position = min((normalize(body).find(t) for t in query_terms if t in normalize(body)), default=0)
            if score:
                node = node_by_id(tree, node_id)
                location = (tree['assets'][asset_id]['location'] if asset_id else
                            next((m['location'] for m in locations if m['start'] <= node['start'] < m['end']), None))
                a = max(0, position - 100)
                results.append({'doc_id': doc_id, 'doc_revision': doc['doc_revision'], 'name': doc['name'],
                                'node_id': node_id, 'asset_id': asset_id, 'evidence_type': kind,
                                'score': score, 'snippet': body[a:a + 500], 'location': location,
                                'snippet_truncated': len(body) > 500, 'candidate_only': True})
    results.sort(key=lambda i: (-i['score'], i['doc_id'], i['node_id'], i['asset_id'] or ''))
    scope = fingerprint([kb_id, query, match, doc_ids])
    return response(root, value, page(results, scope, value['kb_revision'], cursor, limit))


def get_document_outline(settings, kb_id, doc_id, parent_node_id=None, cursor=None, limit=20,
                         expected_kb_revision=None):
    root, value = mounted(settings, kb_id)
    check_revision(value, expected_kb_revision)
    doc = document(value, doc_id)
    _, tree, _, derived = bundle(root, doc)
    if parent_node_id is not None:
        node_by_id(tree, parent_node_id)
    items = []
    for node in tree['nodes']:
        if node['parent_id'] == parent_node_id:
            summary = derived['items'].get(node['node_id'])
            items.append({**{k: v for k, v in node.items() if k != 'children'},
                          'child_count': len(node['children']), 'body_characters': node['end'] - node['start'],
                          'summary': summary['text'] if summary else None, 'summary_is_evidence': False})
    result = page(items, f'{kb_id}:{doc_id}:outline:{parent_node_id}', value['kb_revision'], cursor, limit)
    return response(root, value, {'doc_id': doc_id, 'doc_revision': doc['doc_revision'], **result})


def evidence(value, doc, node_id, kind, body, start=0, end=0, asset_id=None):
    fields = [value['kb_id'], value['kb_revision'], doc['doc_id'], doc['doc_revision'], doc['source_sha256'],
              node_id, kind, start, end, asset_id, digest(body.encode('utf-8'))]
    ref = 'ev1:' + base64.urlsafe_b64encode(json.dumps(fields).encode()).decode()
    return {'evidence_id': ref, 'kb_id': value['kb_id'], 'kb_revision': value['kb_revision'],
            'doc_id': doc['doc_id'], 'doc_revision': doc['doc_revision'], 'source_sha256': doc['source_sha256'],
            'node_id': node_id, 'name': doc['name'], 'evidence_type': kind}


def read_evidence(settings, kb_id, doc_id, doc_revision, node_id, cursor=None, expected_kb_revision=None):
    root, value = mounted(settings, kb_id)
    check_revision(value, expected_kb_revision)
    doc = document(value, doc_id, doc_revision)
    content, tree, locations, _ = bundle(root, doc)
    node = node_by_id(tree, node_id)
    result = text_page(content, node, locations, f'{kb_id}:{doc_id}:{node_id}', value['kb_revision'], cursor)
    ref = evidence(value, doc, node_id, 'original', result['text'], result['start'], result['end'])
    return response(root, value, {**result, **ref})


def read_asset(settings, kb_id, doc_id, doc_revision, asset_id, expected_kb_revision=None):
    root, value = mounted(settings, kb_id)
    check_revision(value, expected_kb_revision)
    doc = document(value, doc_id, doc_revision)
    _, tree, _, derived = bundle(root, doc)
    asset = tree['assets'].get(asset_id)
    if asset is None:
        raise AppError('INVALID_ARGUMENT', 'asset_id 不属于指定文档版本')
    item = derived['items'][asset_id]
    body = item['text'] + '\n' + '\n'.join(item['visible_text'])
    ref = evidence(value, doc, asset['node_id'], 'image_description', body, asset_id=asset_id)
    return response(root, value, {**ref, 'text': body, 'asset_id': asset_id, 'location': asset['location'],
                                 'derived': item, '_image': image_result(inside(root, doc['path']), asset)})


def validate_evidence(settings, kb_id, expected_kb_revision, evidence_refs):
    if not 1 <= len(evidence_refs) <= 100:
        raise AppError('INVALID_ARGUMENT', '引用列表必须为 1..100 项')
    root, value = mounted(settings, kb_id)
    check_revision(value, expected_kb_revision)
    loaded = {}
    for ref in evidence_refs:
        try:
            if not ref.startswith('ev1:') or len(ref) > 4096:
                raise ValueError()
            fields = json.loads(base64.urlsafe_b64decode(ref[4:]))
            owner, revision, doc_id, doc_rev, source_hash, node_id, kind, start, end, asset_id, _ = fields
            if owner != kb_id or revision != expected_kb_revision:
                raise AppError('STALE_REVISION', '引用不属于该知识库版本')
            doc = document(value, doc_id, doc_rev)
            if doc_id not in loaded:
                loaded[doc_id] = bundle(root, doc)
            content, tree, _, derived = loaded[doc_id]
            node = node_by_id(tree, node_id)
            if kind == 'original':
                if type(start) is not int or type(end) is not int or not node['start'] <= start <= end <= node['end']:
                    raise ValueError()
                if end - start > 12000 or asset_id is not None:
                    raise ValueError()
                body = content[start:end]
            elif kind == 'image_description':
                asset = tree['assets'][asset_id]
                if asset['node_id'] != node_id or start != 0 or end != 0:
                    raise ValueError()
                item = derived['items'][asset_id]
                body = item['text'] + '\n' + '\n'.join(item['visible_text'])
            else:
                raise ValueError()
            expected = evidence(value, doc, node_id, kind, body, start, end, asset_id)['evidence_id']
            if ref != expected or source_hash != doc['source_sha256']:
                raise ValueError()
        except (ValueError, TypeError, KeyError) as exc:
            raise AppError('INVALID_ARGUMENT', '引用无效或内容已被篡改') from exc
    return response(root, value, {'valid': True, 'validated': len(evidence_refs), 'semantic_support_checked': False})
