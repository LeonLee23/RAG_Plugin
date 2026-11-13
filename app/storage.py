"""All path checks, cross-process locks, JSON commits and version reads."""
import base64
import hashlib
import json
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from pydantic import ValidationError

from app.config import MAX_JSON
from app.errors import AppError
from app.schemas import Manifest, Registry


def identifier(value):
    if not re.fullmatch(r'[a-f0-9]{32}', value):
        raise AppError('INVALID_ARGUMENT', 'ID 格式无效')
    return value


def no_links(path):
    path = Path(os.path.abspath(path))
    for component in [*reversed(path.parents), path]:
        if component.is_symlink():
            raise AppError('PATH_OUT_OF_SCOPE', '拒绝符号链接')
        if component.exists():
            attrs = getattr(component.lstat(), 'st_file_attributes', 0)
            if attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise AppError('PATH_OUT_OF_SCOPE', '拒绝 reparse point')
    return path


def entry_path(settings, value):
    path = no_links(value)
    if not any(path.is_relative_to(no_links(root)) for root in settings.allowed_roots):
        raise AppError('PATH_OUT_OF_SCOPE', '路径不在 --allow-root 授权范围')
    return path


def inside(root, relative):
    if not relative or '\\' in relative or ':' in relative or PureWindowsPath(relative).is_absolute():
        raise AppError('PATH_OUT_OF_SCOPE', '包内路径必须为相对路径')
    parts = relative.split('/')
    if any(p in ('', '.', '..') for p in parts) or Path(relative).is_absolute():
        raise AppError('PATH_OUT_OF_SCOPE', '包内路径越界')
    root = no_links(root)
    path = no_links(root.joinpath(*parts))
    if not path.is_relative_to(root):
        raise AppError('PATH_OUT_OF_SCOPE', '包内路径越界')
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    no_links(path)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fingerprint(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def read_json(path, schema, expected_hash=None):
    no_links(path)
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            raise AppError('DOCUMENT_LIMIT', 'JSON 超过大小限制')
        if expected_hash is not None and digest(raw) != expected_hash:
            raise AppError('INDEX_STALE', '读取期间产物已变化')
        return schema.model_validate_json(raw).model_dump(mode='json')
    except (ValidationError, ValueError) as exc:
        raise AppError('FORMAT_UNSUPPORTED', f'文件格式不兼容：{path.name}') from exc


def atomic_json(path, value, schema):
    no_links(path)
    data = schema.model_validate(value).model_dump_json(indent=2).encode('utf-8')
    if len(data) > MAX_JSON:
        raise AppError('DOCUMENT_LIMIT', 'JSON 超过大小限制')
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise AppError('IO_ERROR', f'原子写入失败：{path.name}', retryable=True) from exc
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock(path, timeout=5):
    """Fixed lock file; never delete it. OS releases locks after process death."""
    no_links(path)
    with path.open('a+b') as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise AppError('KB_BUSY', '写任务正在执行，请稍后重试', retryable=True) from exc
                time.sleep(0.03)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def registry(settings):
    path = no_links(settings.state_dir) / 'mounts.json'
    return read_json(path, Registry) if path.exists() else Registry().model_dump()


@contextmanager
def registry_write(settings):
    state = no_links(settings.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    with lock(state / '.registry.lock'):
        value = registry(settings)
        yield value
        value['revision'] += 1
        atomic_json(state / 'mounts.json', value, Registry)


def manifest(root):
    try:
        value = read_json(inside(root, 'manifest.json'), Manifest)
    except FileNotFoundError as exc:
        raise AppError('KB_OFFLINE', '知识库离线、已移动或缺少 manifest') from exc
    for key, doc in value['documents'].items():
        if key != doc['doc_id'] or doc['path'] != f"documents/{key}/{doc['doc_revision']}":
            raise AppError('FORMAT_UNSUPPORTED', '文档归属或版本路径错误')
    return value


def mounted(settings, kb_id, write=False):
    identifier(kb_id)
    mount = registry(settings)['mounts'].get(kb_id)
    if not mount:
        raise AppError('KB_NOT_FOUND', '知识库未挂载')
    if write and mount['access'] != 'read_write':
        raise AppError('KB_READ_ONLY', '知识库为只读挂载')
    root = entry_path(settings, mount['path'])
    value = manifest(root)
    if value['kb_id'] != kb_id:
        raise AppError('KB_ID_CONFLICT', '挂载路径的知识库身份已变化')
    return root, value


@contextmanager
def writing(settings, kb_id):
    # Keep mount changes serialized with commits. Never hold this during parsing.
    # ponytail: shared-state KB writes serialize; split locks only if measured contention warrants it.
    with lock(no_links(settings.state_dir) / '.registry.lock'):
        root, _ = mounted(settings, kb_id, write=True)
        with lock(inside(root, '.write.lock')):
            yield root, manifest(root)


def check_revision(value, expected):
    if expected is not None and value['kb_revision'] != expected:
        raise AppError('STALE_REVISION', '知识库版本已变化，请重新读取')


def finish_read(root, value):
    check_revision(manifest(root), value['kb_revision'])


def commit(root, value):
    value['kb_revision'] += 1
    atomic_json(inside(root, 'manifest.json'), value, Manifest)


def page(items, scope, revision, cursor=None, limit=20):
    if not 1 <= limit <= 100:
        raise AppError('INVALID_ARGUMENT', 'limit 必须为 1..100')
    offset = cursor_offset(cursor, scope, revision)
    selected = items[offset:offset + limit]
    end = offset + len(selected)
    return {'items': selected, 'total': len(items), 'truncated': end < len(items),
            'next_cursor': make_cursor(scope, revision, end) if end < len(items) else None}


def make_cursor(scope, revision, offset):
    return base64.urlsafe_b64encode(json.dumps([scope, revision, offset]).encode()).decode()


def cursor_offset(cursor, scope, revision):
    if cursor is None:
        return 0
    try:
        if len(cursor) > 4096:
            raise ValueError()
        s, r, offset = json.loads(base64.urlsafe_b64decode(cursor))
        if s != scope or r != revision:
            raise AppError('STALE_REVISION', '分页作用域或版本已变化')
        if type(offset) is not int or offset < 0:
            raise ValueError()
        return offset
    except (ValueError, TypeError) as exc:
        raise AppError('INVALID_ARGUMENT', '无效游标') from exc


def operation(value, operation_id, kind, params):
    if not 1 <= len(operation_id) <= 200:
        raise AppError('INVALID_ARGUMENT', 'operation_id 长度须为 1..200')
    fp = fingerprint(params)
    previous = value['operations'].get(operation_id)
    if previous and (previous['kind'] != kind or previous['fingerprint'] != fp):
        raise AppError('OPERATION_CONFLICT', '同一 operation_id 的参数不同')
    return previous, fp
