"""Interactive, durable ingestion. Models run only in the host session."""
import os
import shutil
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from app.config import MAX_UPLOAD
from app.errors import AppError
from app.indexing import build, bundle, hashes
from app.parsers.markdown import image_references
from app.schemas import Derived, Document, Enrichment, Task, Tree
from app.storage import (
    atomic_json,
    commit,
    entry_path,
    file_hash,
    fingerprint,
    finish_read,
    identifier,
    inside,
    lock,
    mounted,
    operation,
    read_json,
    writing,
)


def task_path(root, task_id):
    return inside(root, f'.work/{identifier(task_id)}/task.json')


def load_task(root, value, task_id, attempt=None, mutable=False):
    identifier(task_id)
    summary = value['tasks'].get(task_id)
    if not summary:
        raise AppError('TASK_NOT_FOUND', '任务不存在或不属于该库')
    task = read_json(task_path(root, task_id), Task)
    if (task['kb_id'] != value['kb_id'] or task['task_id'] != task_id
            or task['doc_id'] != summary['doc_id'] or task['operation_id'] != summary['operation_id']):
        raise AppError('TASK_NOT_FOUND', '任务归属错误')
    candidate = task['candidate']
    if candidate and (candidate['doc_id'] != task['doc_id'] or candidate['source_sha256'] != task['source_sha256']
                      or candidate['path'] != f".work/{task_id}/attempt-{task['attempt']}"):
        raise AppError('FORMAT_UNSUPPORTED', '任务产物归属错误')
    receipt = value['operations'].get(task['operation_id'])
    if not receipt or receipt['task_id'] != task_id:
        raise AppError('FORMAT_UNSUPPORTED', '任务缺少操作回执')
    if receipt['result'].get('state') == 'published':
        task['state'] = 'published'
    elif summary['state'] == 'cancelled' or task['doc_id'] in value['deleted']:
        task['state'] = 'cancelled'
    if attempt is not None and task['attempt'] != attempt:
        raise AppError('ATTEMPT_STALE', 'attempt 已过期')
    if mutable and task['state'] in ('cancelled', 'published'):
        raise AppError('ATTEMPT_STALE', '任务已终止，不能写回')
    return task


def save_task(root, value, task):
    atomic_json(task_path(root, task['task_id']), task, Task)
    value['tasks'][task['task_id']]['state'] = task['state']
    commit(root, value)


def check_expected(value, task):
    if task['doc_id'] in value['deleted']:
        raise AppError('ATTEMPT_STALE', '文档已删除，旧任务不得恢复')
    current = value['documents'].get(task['doc_id'])
    revision = current['doc_revision'] if current else None
    if revision != task['expected_doc_revision']:
        raise AppError('STALE_REVISION', '预期旧文档版本已变化')


def verify_inputs(root, task):
    folder = inside(root, f".work/{task['task_id']}/input")
    for name, expected in task['input_hashes'].items():
        if file_hash(inside(folder, name)) != expected:
            raise AppError('INDEX_STALE', '任务输入已改变，必须重新导入')
    if task['input_hashes'].get(f"source.{task['media_type']}") != task['source_sha256']:
        raise AppError('INDEX_STALE', '源哈希不一致')


def public_task(root, value, task):
    result = {k: task[k] for k in ('task_id', 'operation_id', 'kb_id', 'doc_id', 'attempt', 'state',
                                   'source_sha256', 'expected_doc_revision', 'checkpoint', 'error')}
    result['kb_revision'] = value['kb_revision']
    result['pending_images'] = []
    if task['candidate'] and task['state'] in ('awaiting_enrichment', 'ready_to_publish'):
        _, tree, _, derived = bundle(root, task['candidate'])
        result['pending_images'] = [key for key in tree['assets']
                                    if derived['items'].get(key, {}).get('status') not in
                                    ('complete', 'decorative', 'unreadable')]
        result['node_ids'] = [n['node_id'] for n in tree['nodes'][:100]]
        result['nodes_truncated'] = len(tree['nodes']) > 100
        result['coverage'] = task['candidate']['coverage']
    if task['state'] == 'published':
        result['receipt'] = value['operations'][task['operation_id']]['result']
    return result


def get_ingestion_task(settings, kb_id, task_id):
    root, value = mounted(settings, kb_id)
    result = public_task(root, value, load_task(root, value, task_id))
    finish_read(root, value)
    return result


def import_document(settings, kb_id, source_path, operation_id, doc_id=None,
                    expected_doc_revision=None, resume=False):
    """Stage input and return promptly. Caller schedules run_task for queued work."""
    mounted(settings, kb_id, write=True)
    source = entry_path(settings, source_path)
    media = source.suffix.lower().lstrip('.')
    if media not in ('md', 'pdf', 'docx'):
        raise AppError('FORMAT_UNSUPPORTED', '仅支持 MD、PDF、DOCX')
    if doc_id:
        identifier(doc_id)
        if expected_doc_revision is None:
            raise AppError('INVALID_ARGUMENT', '更新须提供 expected_doc_revision')
    elif expected_doc_revision is not None:
        raise AppError('INVALID_ARGUMENT', '新增文档不能指定旧版本')
    if resume:
        root, value = mounted(settings, kb_id, write=True)
        previous = value['operations'].get(operation_id)
        if not previous or previous['kind'] != 'import' or not previous['task_id']:
            raise AppError('TASK_NOT_FOUND', '该 operation_id 没有可恢复任务')
        task = load_task(root, value, previous['task_id'])
        operation(value, operation_id, 'import',
                  [str(source), task['input_hashes'], doc_id, expected_doc_revision])
        if task['state'] == 'published':
            return previous['result']
        return resume_task(settings, kb_id, previous['task_id'])
    # Read bounded input outside KB lock, preserving a coherent byte snapshot.
    if not source.is_file():
        raise AppError('IO_ERROR', '源路径不是可读的常规文件')
    with source.open('rb') as stream:
        raw = stream.read(MAX_UPLOAD + 1)
    if not raw or len(raw) > MAX_UPLOAD:
        raise AppError('DOCUMENT_LIMIT', '源文件为空或超过 20 MiB')
    from app.storage import digest
    source_hash = digest(raw)
    resources = {}
    if media == 'md':
        try:
            refs = image_references(raw.decode('utf-8'))
        except UnicodeError as exc:
            raise AppError('PARSE_FAILED', 'Markdown 必须为 UTF-8') from exc
        for relative, _ in refs:
            if relative == 'source.md':
                raise AppError('INVALID_ARGUMENT', '图片与内部 source.md 名称冲突')
            path = inside(source.parent, relative)
            with path.open('rb') as stream:
                image = stream.read(MAX_UPLOAD + 1)
            if len(image) > MAX_UPLOAD:
                raise AppError('DOCUMENT_LIMIT', '图片超过 20 MiB')
            resources[relative] = image
            if sum(len(data) for data in resources.values()) > 100 * 1024 * 1024:
                raise AppError('DOCUMENT_LIMIT', 'Markdown 图片总大小超过 100 MiB')
    inputs = {f'source.{media}': raw, **resources}
    input_hashes = {name: digest(data) for name, data in inputs.items()}
    params = [str(source), input_hashes, doc_id, expected_doc_revision]
    with writing(settings, kb_id) as (root, value):
        previous, fp = operation(value, operation_id, 'import', params)
        if previous:
            return previous['result'] or public_task(root, value, load_task(root, value, previous['task_id']))
        if not doc_id:
            for doc in value['documents'].values():
                if doc['input_sha256'] == fingerprint(input_hashes):
                    # Ensure modified packages are not mistaken for a successful duplicate.
                    bundle(root, doc)
                    result = {'kb_id': kb_id, 'doc_id': doc['doc_id'], 'doc_revision': doc['doc_revision'],
                              'state': 'published', 'duplicate': True}
                    value['operations'][operation_id] = {'kind': 'import', 'fingerprint': fp, 'result': result}
                    commit(root, value)
                    return result
            for existing_id, summary in value['tasks'].items():
                if summary['state'] in ('published', 'cancelled'):
                    continue
                existing = load_task(root, value, existing_id)
                if existing['expected_doc_revision'] is None and existing['input_hashes'] == input_hashes:
                    value['operations'][operation_id] = {'kind': 'import', 'fingerprint': fp,
                                                         'task_id': existing_id, 'result': {}}
                    commit(root, value)
                    return public_task(root, value, existing)
        task_id = uuid4().hex
        task = Task(task_id=task_id, operation_id=operation_id, kb_id=kb_id, doc_id=doc_id or uuid4().hex,
                    source_sha256=source_hash, expected_doc_revision=expected_doc_revision,
                    media_type=media, name=source.name, state='queued').model_dump()
        check_expected(value, task)
        folder = inside(root, f'.work/{task_id}/input')
        folder.mkdir(parents=True)
        for relative, data in inputs.items():
            dest = inside(folder, relative)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            task['input_hashes'][relative] = digest(data)
        atomic_json(task_path(root, task_id), task, Task)
        value['operations'][operation_id] = {'kind': 'import', 'fingerprint': fp, 'task_id': task_id, 'result': {}}
        value['tasks'][task_id] = {'doc_id': task['doc_id'], 'operation_id': operation_id, 'state': 'queued'}
        commit(root, value)
    return public_task(root, value, task)


def resume_task(settings, kb_id, task_id):
    root, _ = mounted(settings, kb_id, write=True)
    with lock(inside(root, f'.work/{identifier(task_id)}/.task.lock'), timeout=0):
        with writing(settings, kb_id) as (root, value):
            task = load_task(root, value, task_id, mutable=True)
            check_expected(value, task)
            verify_inputs(root, task)
            task['attempt'] += 1
            task.update(state='queued', error=None, candidate=None, checkpoint='resume')
            save_task(root, value, task)
    return public_task(root, value, task)


def run_task(settings, kb_id, task_id):
    root, value = mounted(settings, kb_id, write=True)
    load_task(root, value, task_id)
    with lock(inside(root, f'.work/{task_id}/.task.lock'), timeout=0):
        with writing(settings, kb_id) as (root, value):
            task = load_task(root, value, task_id, mutable=True)
            if task['state'] != 'queued':
                return
            task.update(state='parsing', checkpoint='input_copied')
            save_task(root, value, task)
        try:
            verify_inputs(root, task)
            output = inside(root, f".work/{task_id}/attempt-{task['attempt']}")
            output.mkdir()
            source = inside(root, f".work/{task_id}/input/source.{task['media_type']}")
            shutil.copyfile(source, inside(output, source.name))
            dependencies = {}
            for name in task['input_hashes']:
                if name == source.name:
                    continue
                target = inside(output, f'originals/{name}')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(inside(source.parent, name), target)
                dependencies[name] = f'originals/{name}'
            build(source, task['media_type'], output)
            tree = read_json(inside(output, 'tree.json'), Tree)
            artifact_hashes = hashes(output, tree['assets'])
            artifact_hashes.update({p: file_hash(inside(output, p)) for p in dependencies.values()})
            task['candidate'] = Document(doc_id=task['doc_id'], name=task['name'], media_type=task['media_type'],
                doc_revision=uuid4().hex, path=output.relative_to(root).as_posix(),
                source_sha256=task['source_sha256'], input_sha256=fingerprint(task['input_hashes']),
                source_dependencies=dependencies, hashes=artifact_hashes,
                coverage={'images_total': len(tree['assets']), 'images_processed': 0}).model_dump()
            task.update(state='awaiting_enrichment' if tree['assets'] else 'ready_to_publish', checkpoint='parsed')
        except Exception as exc:
            error = exc if isinstance(exc, AppError) else AppError('PARSE_FAILED', f'解析失败：{type(exc).__name__}')
            task.update(state='failed', error=error.payload()['error'])
        with writing(settings, kb_id) as (root, value):
            load_task(root, value, task_id, task['attempt'], mutable=True)
            check_expected(value, task)
            save_task(root, value, task)


def read_ingestion_input(settings, kb_id, task_id, attempt, item_id, cursor=None):
    from app.retrieval.search import image_result, text_page
    root, value = mounted(settings, kb_id)
    task = load_task(root, value, task_id, attempt)
    if task['state'] not in ('awaiting_enrichment', 'ready_to_publish'):
        raise AppError('INVALID_ARGUMENT', '任务尚无可读取的解析产物')
    content, tree, locations, _ = bundle(root, task['candidate'])
    result = {'kb_id': kb_id, 'task_id': task_id, 'attempt': attempt, 'item_id': item_id,
              'source_sha256': task['source_sha256'], 'staged': True}
    if item_id in tree['assets']:
        asset = tree['assets'][item_id]
        result.update(source_sha256=asset['sha256'], location=asset['location'],
                      context=content[max(0, asset['offset'] - 2000):asset['offset'] + 2000])
        result['_image'] = image_result(inside(root, task['candidate']['path']), asset)
    else:
        node = next((n for n in tree['nodes'] if n['node_id'] == item_id), None)
        if not node:
            raise AppError('INVALID_ARGUMENT', '节点/图片不属于该任务')
        result.update(text_page(content, node, locations, f'{kb_id}:{task_id}:{item_id}',
                                [attempt, task['candidate']['hashes']['content.md']], cursor))
    finish_read(root, value)
    return result


def submit_enrichment(settings, kb_id, task_id, attempt, items):
    if not 1 <= len(items) <= 20:
        raise AppError('INVALID_ARGUMENT', '每批需为 1..20 项')
    try:
        items = [Enrichment.model_validate(i).model_dump() for i in items]
    except ValidationError as exc:
        raise AppError('INVALID_ARGUMENT', '派生结果不符合 Schema') from exc
    if len({i['item_id'] for i in items}) != len(items):
        raise AppError('INVALID_ARGUMENT', '批次中 item_id 重复')
    root, value = mounted(settings, kb_id, write=True)
    load_task(root, value, task_id)
    with lock(inside(root, f'.work/{task_id}/.task.lock'), timeout=0):
        with writing(settings, kb_id) as (root, value):
            task = load_task(root, value, task_id, attempt, mutable=True)
            check_expected(value, task)
            verify_inputs(root, task)
            if task['state'] not in ('awaiting_enrichment', 'ready_to_publish'):
                raise AppError('INVALID_ARGUMENT', '任务未完成解析')
            content, tree, _, derived = bundle(root, task['candidate'])
            nodes = {n['node_id']: n for n in tree['nodes']}
            for item in items:
                key = item['item_id']
                if sum(len(s) for s in item['visible_text'] + item['uncertain']) + len(item['text']) > 4000:
                    raise AppError('DOCUMENT_LIMIT', '单条派生文本总计超过 4000 字符')
                if item['kind'] == 'image':
                    owner = tree['assets'].get(key)
                    expected = owner['sha256'] if owner else None
                else:
                    owner = nodes.get(key)
                    expected = task['source_sha256'] if owner else None
                    if item['quote'] and owner and item['quote'] not in content[owner['start']:owner['end']]:
                        raise AppError('INVALID_ARGUMENT', '逐字摘录不匹配节点原文')
                if expected is None or item['source_sha256'] != expected:
                    raise AppError('INDEX_STALE', '派生项归属或源哈希不匹配')
                old = derived['items'].get(key)
                if old and old != item and old['status'] != 'failed':
                    raise AppError('OPERATION_CONFLICT', '已提交项不同；恢复任务后重新处理')
                derived['items'][key] = item
                derived['generated_at'].setdefault(key, datetime.now(timezone.utc).isoformat())
            coverage = {'images_total': len(tree['assets']), 'images_processed': 0, 'unreadable': 0, 'decorative': 0}
            for key in tree['assets']:
                status = derived['items'].get(key, {}).get('status')
                if status in ('complete', 'unreadable', 'decorative'):
                    coverage['images_processed'] += 1
                    if status != 'complete':
                        coverage[status] += 1
            folder = inside(root, task['candidate']['path'])
            atomic_json(inside(folder, 'derived.json'), derived, Derived)
            task['candidate']['hashes']['derived.json'] = file_hash(inside(folder, 'derived.json'))
            task['candidate']['coverage'] = coverage
            task['state'] = ('ready_to_publish' if coverage['images_processed'] == coverage['images_total']
                             else 'awaiting_enrichment')
            save_task(root, value, task)
    return public_task(root, value, task)


def publish_document(settings, kb_id, task_id, attempt, expected_doc_revision=None):
    root, value = mounted(settings, kb_id, write=True)
    load_task(root, value, task_id)
    with lock(inside(root, f'.work/{task_id}/.task.lock'), timeout=0):
        with writing(settings, kb_id) as (root, value):
            task = load_task(root, value, task_id, attempt)
            if task['state'] == 'published':
                return value['operations'][task['operation_id']]['result']
            load_task(root, value, task_id, attempt, mutable=True)
            check_expected(value, task)
            if task['expected_doc_revision'] != expected_doc_revision:
                raise AppError('STALE_REVISION', '发布须携带任务的 expected_doc_revision')
            if task['state'] != 'ready_to_publish':
                raise AppError('ENRICHMENT_REQUIRED', '任务未完成，图片未处理或处理失败')
            verify_inputs(root, task)
            doc = dict(task['candidate'])
            target_rel = f"documents/{task['doc_id']}/{doc['doc_revision']}"
            target = inside(root, target_rel)
            if target.exists():
                # Recovery after directory rename but before manifest commit.
                doc['path'] = target_rel
            _, tree, _, derived = bundle(root, doc)
            if any(derived['items'].get(k, {}).get('status') not in ('complete', 'unreadable', 'decorative')
                   for k in tree['assets']):
                raise AppError('ENRICHMENT_REQUIRED', '存在未完成图片')
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.rename(inside(root, doc['path']), target)
            doc['path'] = target_rel
            value['documents'][doc['doc_id']] = doc
            result = {'kb_id': kb_id, 'doc_id': doc['doc_id'], 'doc_revision': doc['doc_revision'],
                      'kb_revision': value['kb_revision'] + 1, 'task_id': task_id, 'attempt': attempt,
                      'state': 'published', 'coverage': doc['coverage']}
            value['operations'][task['operation_id']]['result'] = result
            value['tasks'][task_id]['state'] = 'published'
            commit(root, value)
            # Commit receipt is authoritative, even if the process exits here.
    return result


def cancel_ingestion(settings, kb_id, task_id, attempt):
    # Invalidate under KB lock without waiting on parser's task lock.
    with writing(settings, kb_id) as (root, value):
        task = load_task(root, value, task_id, attempt)
        if task['state'] in ('published', 'cancelled'):
            return public_task(root, value, task)
        task['state'] = 'cancelled'
        save_task(root, value, task)
    return public_task(root, value, task)
