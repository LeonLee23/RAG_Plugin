"""Knowledge-base creation, local mounts and logical document lifecycle."""
from uuid import uuid4

from app.errors import AppError
from app.indexing import bundle
from app.schemas import Manifest
from app.storage import (
    atomic_json,
    commit,
    entry_path,
    finish_read,
    inside,
    lock,
    manifest,
    mounted,
    operation,
    page,
    registry,
    registry_write,
    writing,
)

ACTIVE = {'queued', 'parsing', 'awaiting_enrichment', 'ready_to_publish', 'failed'}


def busy(value):
    return any(t['state'] in ACTIVE for t in value['tasks'].values())


def create_knowledge_base(settings, path, name, description, operation_id):
    root = entry_path(settings, path)
    if not name.strip():
        raise AppError('INVALID_ARGUMENT', '知识库名称不能为空')
    params = {'path': str(root), 'name': name, 'description': description}
    with registry_write(settings) as reg:
        root.mkdir(parents=True, exist_ok=True)
        if not (root / 'manifest.json').exists() and any(p.name != '.write.lock' for p in root.iterdir()):
            raise AppError('OPERATION_CONFLICT', '建库仅允许新目录或空目录')
        with lock(inside(root, '.write.lock')):
            if (root / 'manifest.json').exists():
                value = manifest(root)
                previous, _ = operation(value, operation_id, 'create', params)
                if not previous:
                    raise AppError('OPERATION_CONFLICT', '该目录已有知识库')
            else:
                value = Manifest(kb_id=uuid4().hex, name=name, description=description).model_dump()
                _, fp = operation(value, operation_id, 'create', params)
                value['operations'][operation_id] = {'kind': 'create', 'fingerprint': fp,
                                                     'result': {'kb_id': value['kb_id']}}
                inside(root, 'documents').mkdir()
                inside(root, '.work').mkdir()
                atomic_json(inside(root, 'manifest.json'), value, Manifest)
            old = reg['mounts'].get(value['kb_id'])
            if old and old['path'] != str(root):
                raise AppError('KB_ID_CONFLICT', '同一 kb_id 已挂载其他路径')
            reg['mounts'][value['kb_id']] = {'path': str(root), 'access': 'read_write'}
    return {'kb_id': value['kb_id'], 'kb_revision': value['kb_revision'], 'access': 'read_write'}


def mount_knowledge_base(settings, path, access='read_only', replace=False):
    if access not in ('read_only', 'read_write'):
        raise AppError('INVALID_ARGUMENT', 'access 无效')
    root = entry_path(settings, path)
    value = manifest(root)
    # A read-only mount never creates a lock or cache inside the package.
    for doc in value['documents'].values():
        bundle(root, doc)
    with registry_write(settings) as reg:
        old = reg['mounts'].get(value['kb_id'])
        if old and old['path'] != str(root) and not replace:
            raise AppError('KB_ID_CONFLICT', '同一 kb_id 已在另一目录挂载；需显式 replace')
        promoting = old and old['path'] == str(root) and old['access'] == 'read_only' and access == 'read_write'
        if old and old != {'path': str(root), 'access': access} and not promoting:
            try:
                old_value = manifest(entry_path(settings, old['path']))
            except AppError as exc:
                if exc.code != 'KB_OFFLINE':
                    raise
            else:
                if busy(old_value):
                    raise AppError('KB_BUSY', '先发布或取消现有任务，再切换挂载')
        finish_read(root, value)
        reg['mounts'][value['kb_id']] = {'path': str(root), 'access': access}
    return {'kb_id': value['kb_id'], 'kb_revision': value['kb_revision'], 'access': access,
            'documents': len(value['documents']),
            'coverage': {key: sum(d['coverage'].get(key, 0) for d in value['documents'].values())
                         for key in ('images_total', 'images_processed', 'unreadable', 'decorative')}}


def unmount_knowledge_base(settings, kb_id):
    with registry_write(settings) as reg:
        mount = reg['mounts'].get(kb_id)
        if not mount:
            raise AppError('KB_NOT_FOUND', '知识库未挂载')
        root = entry_path(settings, mount['path'])
        try:
            value = manifest(root)
        except AppError as exc:
            if exc.code != 'KB_OFFLINE':
                raise
        else:
            if busy(value):
                raise AppError('KB_BUSY', '先发布或取消现有任务')
        del reg['mounts'][kb_id]
    return {'kb_id': kb_id, 'unmounted': True}


def list_knowledge_bases(settings, cursor=None, limit=20):
    reg = registry(settings)
    items = []
    revisions = []
    for kb_id, mount in sorted(reg['mounts'].items()):
        item = {'kb_id': kb_id, **mount}
        try:
            _, value = mounted(settings, kb_id)
            item.update(name=value['name'], description=value['description'],
                        kb_revision=value['kb_revision'], available=True)
            revisions.append([kb_id, value['kb_revision']])
        except AppError as exc:
            item.update(available=False, error=exc.payload()['error'])
            revisions.append([kb_id, exc.code])
        items.append(item)
    result = page(items, 'mounts', [reg['revision'], revisions], cursor, limit)
    if registry(settings)['revision'] != reg['revision']:
        raise AppError('STALE_REVISION', '挂载清单已变化')
    return result


def list_documents(settings, kb_id, cursor=None, limit=20, expected_kb_revision=None):
    from app.storage import check_revision
    root, value = mounted(settings, kb_id)
    check_revision(value, expected_kb_revision)
    entries = []
    for doc in value['documents'].values():
        entry = {k: v for k, v in doc.items() if k not in ('hashes', 'path')}
        try:
            bundle(root, doc)
            entry['index_valid'] = True
        except AppError as exc:
            entry.update(index_valid=False, error=exc.payload()['error'])
        entry['tasks'] = [{'task_id': k, **t} for k, t in value['tasks'].items()
                          if t['doc_id'] == doc['doc_id'] and t['state'] in ACTIVE][:20]
        entry['tasks_truncated'] = sum(t['doc_id'] == doc['doc_id'] and t['state'] in ACTIVE
                                       for t in value['tasks'].values()) > 20
        entries.append(entry)
    result = page(entries, f'{kb_id}:documents', value['kb_revision'], cursor, limit)
    finish_read(root, value)
    return {'kb_id': kb_id, 'kb_revision': value['kb_revision'], **result}


def delete_document(settings, kb_id, doc_id, expected_doc_revision, operation_id):
    with writing(settings, kb_id) as (root, value):
        previous, fp = operation(value, operation_id, 'delete', [doc_id, expected_doc_revision])
        if previous:
            return previous['result']
        doc = value['documents'].get(doc_id)
        if not doc:
            raise AppError('DOCUMENT_NOT_FOUND', '文档不存在或不属于该库')
        if doc['doc_revision'] != expected_doc_revision:
            raise AppError('STALE_REVISION', '文档版本已变化')
        del value['documents'][doc_id]
        value['deleted'][doc_id] = expected_doc_revision
        for task in value['tasks'].values():
            if task['doc_id'] == doc_id and task['state'] != 'published':
                task['state'] = 'cancelled'
        result = {'kb_id': kb_id, 'doc_id': doc_id, 'deleted': True, 'kb_revision': value['kb_revision'] + 1}
        value['operations'][operation_id] = {'kind': 'delete', 'fingerprint': fp, 'result': result}
        commit(root, value)
    # Deliberately retain unreferenced files. No physical delete tool in V2.0.
    return result
