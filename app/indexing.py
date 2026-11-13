"""Deterministic section tree with direct text ranges and original locations."""
from pathlib import Path

from app.config import MAX_JSON, MAX_TEXT, MAX_UPLOAD
from app.errors import AppError
from app.parsers import parse_document
from app.schemas import Derived, Locations, Tree
from app.storage import atomic_json, digest, file_hash, fingerprint, inside, read_json


def build(source, media_type, output):
    blocks, assets = parse_document(source, media_type, inside(output, 'assets'))
    nodes = [dict(node_id='n0', parent_id=None, title=source.name, order=0, start=0, end=0,
                  children=[], structure_quality='paragraphs')]
    stack = [(0, nodes[0])]
    current = nodes[0]
    text_parts, mappings = [], []
    offset = 0
    has_headings = any(b.heading_level for b in blocks)
    for block in blocks:
        level = block.heading_level
        fallback = media_type == 'docx' and not has_headings and block.kind == 'text'
        if level or fallback:
            level = level or 1
            while len(stack) > 1 and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1]
            title = (block.heading_path[-1] if block.heading_path else block.text)[:1000]
            if fallback:
                title = f"{block.location['part']} 段落 {block.location['paragraph']}"
            current = dict(node_id=f'n{len(nodes)}', parent_id=parent['node_id'], title=title,
                           order=len(nodes), start=offset, end=offset, children=[],
                           structure_quality='paragraphs' if fallback else block.structure_quality)
            parent['children'].append(current['node_id'])
            nodes.append(current)
            stack.append((level, current))
        if block.kind == 'text':
            piece = block.text if media_type == 'md' else block.text + '\n\n'
            text_parts.append(piece)
            mappings.append({'start': offset, 'end': offset + len(piece), 'location': block.location})
            offset += len(piece)
            current['end'] = offset
        if block.asset_id:
            assets[block.asset_id]['node_id'] = current['node_id']
            assets[block.asset_id]['offset'] = offset
    content = ''.join(text_parts)
    if len(content) > MAX_TEXT:
        raise AppError('DOCUMENT_LIMIT', '正文超过 200 万字符')
    for key, asset in assets.items():
        asset['asset_id'] = key
        asset['sha256'] = file_hash(Path(asset['path']))
        asset['path'] = f'assets/{key}.png'
        if media_type == 'md':
            line = asset['location']['line_start']
            position = sum(len(s) for s in content.splitlines(keepends=True)[:line-1])
            asset['node_id'] = next((n['node_id'] for n in nodes if n['start'] <= position < n['end']), 'n0')
            asset['offset'] = position
    inside(output, 'content.md').write_text(content, encoding='utf-8', newline='')
    atomic_json(inside(output, 'tree.json'), {'nodes': nodes, 'assets': assets}, Tree)
    atomic_json(inside(output, 'locations.json'), {'mappings': mappings}, Locations)
    atomic_json(inside(output, 'derived.json'), Derived().model_dump(), Derived)


def bundle(root, doc):
    """Read the whole bounded document once, verifying every declared artifact."""
    folder = inside(root, doc['path'])
    required = {f"source.{doc['media_type']}", 'content.md', 'tree.json', 'locations.json', 'derived.json'}
    if not required <= doc['hashes'].keys():
        raise AppError('INDEX_STALE', '产物哈希清单不完整')
    if doc['hashes'][f"source.{doc['media_type']}"] != doc['source_sha256']:
        raise AppError('INDEX_STALE', '源哈希不一致')
    for original, relative in doc['source_dependencies'].items():
        inside(folder, original)
        if relative != f'originals/{original}' or relative not in doc['hashes']:
            raise AppError('INDEX_STALE', 'Markdown 原始图片映射无效')
    inputs = {f"source.{doc['media_type']}": doc['source_sha256']}
    inputs.update({key: doc['hashes'][path] for key, path in doc['source_dependencies'].items()})
    if fingerprint(inputs) != doc['input_sha256']:
        raise AppError('INDEX_STALE', '完整输入哈希不一致')
    try:
        for relative, expected in doc['hashes'].items():
            path = inside(folder, relative)
            maximum = (160 * 1024 * 1024 if relative.startswith('assets/') else
                       MAX_JSON if relative.endswith('.json') else
                       4 * MAX_TEXT if relative == 'content.md' else MAX_UPLOAD)
            if not path.is_file() or path.stat().st_size > maximum:
                raise AppError('DOCUMENT_LIMIT', '包内产物大小或文件类型无效')
            if file_hash(path) != expected:
                raise AppError('INDEX_STALE', '源文件或产物已修改，请用外部原文更新重建')
        tree = read_json(inside(folder, 'tree.json'), Tree, doc['hashes']['tree.json'])
        locations = read_json(inside(folder, 'locations.json'), Locations, doc['hashes']['locations.json'])['mappings']
        derived = read_json(inside(folder, 'derived.json'), Derived, doc['hashes']['derived.json'])
        with inside(folder, 'content.md').open('rb') as stream:
            raw = stream.read(4 * MAX_TEXT + 1)
        if digest(raw) != doc['hashes']['content.md']:
            raise AppError('INDEX_STALE', '读取期间正文已变化')
        content = raw.decode('utf-8')
    except (OSError, UnicodeError) as exc:
        raise AppError('INDEX_STALE', '已发布文件缺失或不可读') from exc
    if len(content) > MAX_TEXT:
        raise AppError('DOCUMENT_LIMIT', '正文超过上限')
    node_ids = {n['node_id'] for n in tree['nodes']}
    if len(node_ids) != len(tree['nodes']) or not tree['nodes'] or tree['nodes'][0]['node_id'] != 'n0':
        raise AppError('FORMAT_UNSUPPORTED', '重复 node_id')
    nodes = {n['node_id']: n for n in tree['nodes']}
    for node in tree['nodes']:
        if (not 0 <= node['start'] <= node['end'] <= len(content)
                or not set(node['children']) <= node_ids
                or (node['parent_id'] is not None and node['parent_id'] not in node_ids)):
            raise AppError('FORMAT_UNSUPPORTED', '节点范围或归属错误')
        if node['parent_id'] is None:
            if node['node_id'] != 'n0':
                raise AppError('FORMAT_UNSUPPORTED', '只能存在一个根节点')
        elif (nodes[node['parent_id']]['order'] >= node['order']
              or node['node_id'] not in nodes[node['parent_id']]['children']):
            raise AppError('FORMAT_UNSUPPORTED', '目录父子关系无效')
        if any(nodes[child]['parent_id'] != node['node_id'] for child in node['children']):
            raise AppError('FORMAT_UNSUPPORTED', '目录子节点归属错误')
    for mapping in locations:
        if not 0 <= mapping['start'] <= mapping['end'] <= len(content):
            raise AppError('FORMAT_UNSUPPORTED', '来源区间错误')
    for key, asset in tree['assets'].items():
        if (asset['asset_id'] != key or asset['node_id'] not in node_ids or asset['offset'] > len(content)
                or asset['path'] != f'assets/{key}.png'
                or doc['hashes'].get(asset['path']) != asset['sha256']):
            raise AppError('INDEX_STALE', '图片归属或哈希错误')
    for key, item in derived['items'].items():
        owner = tree['assets'].get(key) if item['kind'] == 'image' else None
        expected = owner['sha256'] if owner else doc['source_sha256']
        if (item['item_id'] != key or item['source_sha256'] != expected
                or (item['kind'] == 'image' and owner is None)
                or (item['kind'] == 'summary' and key not in node_ids)):
            raise AppError('INDEX_STALE', '派生结果归属或哈希错误')
    if not doc['path'].startswith('.work/') and any(
            derived['items'].get(key, {}).get('status') not in ('complete', 'unreadable', 'decorative')
            for key in tree['assets']):
        raise AppError('INDEX_STALE', '已发布文档图片处理不完整')
    return content, tree, locations, derived


def hashes(folder, assets):
    names = ['content.md', 'tree.json', 'locations.json', 'derived.json']
    names += [a['path'] for a in assets.values()]
    names += [p.name for p in folder.glob('source.*')]
    return {name: file_hash(inside(folder, name)) for name in names}
