import io
import json
import sys
import threading
from pathlib import Path

import psutil
import pytest
from docx import Document
from pydantic import BaseModel, ConfigDict, ValidationError

from app import knowledge
from app.answering import answer
from app.config import Settings
from app.errors import AppError
from app.ingestion import Worker, validate_relations
from app.models import AgentRunner, run_bounded
from app.retrieval.search import search
from app.schemas import AskRequest, GeneratedAnswer, GraphResult, SearchRequest
from tests.test_integration import prepare, publish


class Output(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: str


def test_real_subprocess_schema_repair_and_auth(tmp_path):
    script = tmp_path/'fake_cli.py'
    counter = tmp_path/'calls'
    script.write_text('''import pathlib, sys
mode, counter = sys.argv[1:3]
p = pathlib.Path(counter)
n = int(p.read_text())+1 if p.exists() else 1
p.write_text(str(n))
sys.stdin.buffer.read()
if mode == 'auth':
    print('Not logged in');sys.exit(1)
out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
out.write_text('{"value":"ok"}' if mode == 'repair' and n == 2 else '{broken')
''', encoding='utf-8')
    settings = Settings(agent_provider_1='codex', agent_provider_2='', _env_file=None)
    runner = AgentRunner(settings, [sys.executable, str(script), 'repair', str(counter)])
    assert runner.generate_json('probe', {}, Output).value == 'ok'
    assert counter.read_text() == '2'
    counter.unlink()
    runner.command = [sys.executable, str(script), 'broken', str(counter)]
    with pytest.raises(AppError) as error:
        runner.generate_json('probe', {}, Output)
    assert error.value.code == 'MODEL_OUTPUT_INVALID' and counter.read_text() == '2'
    counter.unlink()
    runner.command = [sys.executable, str(script), 'auth', str(counter)]
    with pytest.raises(AppError) as error:
        runner.generate_json('probe', {}, Output)
    assert error.value.code == 'MODEL_AUTH_REQUIRED' and counter.read_text() == '1'
    assert runner.slot.acquire(blocking=False)
    runner.slot.release()


@pytest.mark.parametrize('failure', ['schema', 'evidence'])
def test_model_repair_receives_specific_error_without_logging_input(monkeypatch, caplog, failure):
    runner = AgentRunner(Settings(agent_provider_1='codex', agent_provider_2='', _env_file=None), ['fake'])
    calls = []
    reason = 'string_type' if failure == 'schema' else 'Quote is not in source'

    def invoke(provider, task, payload, schema, image, repair, timeout, cancelled):
        calls.append(repair)
        if repair:
            assert reason in repair['error']
            assert repair['previous_output'] == {
                'value': {'source': 'private-source-text'} if failure == 'schema' else 'paraphrase'}
            return {'value': 'original'}
        return {'value': {'source': 'private-source-text'} if failure == 'schema' else 'paraphrase'}

    def validate(result):
        if result.value != 'original':
            raise ValueError('Quote is not in source')

    monkeypatch.setattr(runner, '_invoke', invoke)
    assert runner.generate_json('probe', {}, Output, validate=validate).value == 'original'
    assert len(calls) == 2
    assert 'private-source-text' not in caplog.text
    assert reason in caplog.text


def test_provider_settings_support_legacy_env(monkeypatch, tmp_path):
    for key in ('AGENT_PROVIDER', 'AGENT_PROVIDER_1', 'AGENT_PROVIDER_2'):
        monkeypatch.delenv(key, raising=False)
    assert Settings(_env_file=None).agent_providers == ('codex', 'claude')
    env_file = tmp_path / '.env'
    env_file.write_text('AGENT_PROVIDER=claude\nAGENT_PROVIDER_2=codex\n', encoding='utf-8')
    assert Settings(_env_file=env_file).agent_providers == ('claude', 'codex')
    monkeypatch.setenv('AGENT_PROVIDER_1', 'codex')
    assert Settings(_env_file=env_file).agent_providers == ('codex',)
    monkeypatch.setenv('AGENT_PROVIDER_2', '')
    assert Settings(_env_file=env_file).agent_providers == ('codex',)


@pytest.mark.parametrize(('failure', 'expected_calls'), [
    ('none', ['codex status', 'codex generate']),
    ('missing', ['claude status', 'claude generate']),
    ('auth', ['codex status', 'claude status', 'claude generate']),
    ('quota', ['codex status', 'codex generate', 'claude status', 'claude generate']),
    ('timeout', ['codex status', 'codex generate', 'claude status', 'claude generate']),
    ('both', ['codex status', 'codex generate', 'claude status', 'claude generate']),
])
def test_provider_priority_with_cli_subprocesses(tmp_path, monkeypatch, failure, expected_calls):
    script = tmp_path / 'fake_cli.py'
    calls_path = tmp_path / 'calls.jsonl'
    script.write_text('''import json, pathlib, sys, time
provider, failure, calls, *args = sys.argv[1:]
status = args[-1] == 'status'
prompt = sys.stdin.buffer.read().decode('utf-8')
with open(calls, 'a', encoding='utf-8') as file:
    file.write(json.dumps([provider + (' status' if status else ' generate'), args, prompt]) + '\\n')
if status:
    if provider == 'codex' and failure == 'auth':
        print('Not logged in')
        sys.exit(1)
    print('{"loggedIn":true}')
elif provider == 'codex':
    if failure in ('quota', 'both'):
        print('usage limit reached')
        sys.exit(1)
    if failure == 'timeout':
        time.sleep(60)
    output = pathlib.Path(args[args.index('--output-last-message')+1])
    output.write_text('{"value":"codex"}', encoding='utf-8')
else:
    if failure == 'both':
        print('authentication failed')
        sys.exit(1)
    print('{"structured_output":{"value":"claude"}}')
''', encoding='utf-8')

    def fake_cli(provider):
        if provider == 'codex' and failure == 'missing':
            raise AppError('MODEL_UNAVAILABLE', 'Missing test CLI', 503)
        return [sys.executable, str(script), provider, failure, str(calls_path)]

    monkeypatch.setattr('app.models.resolve_cli', fake_cli)
    settings = Settings(agent_provider_1='codex', agent_provider_2='claude',
                        agent_model='codex-probe', agent_timeout_seconds=1, _env_file=None)
    runner = AgentRunner(settings)
    payload = {'question': '凤凰项目使用什么数据库？', 'evidence': ['星河数据库']}
    providers_used = set()
    if failure == 'both':
        with pytest.raises(AppError) as error:
            runner.generate_json('probe', payload, Output, on_provider=providers_used.add)
        assert error.value.code == runner.last_error == 'MODEL_AUTH_REQUIRED'
        assert runner.last_provider is None and not providers_used
    else:
        expected_provider = 'codex' if failure == 'none' else 'claude'
        assert runner.generate_json('probe', payload, Output, on_provider=providers_used.add).value == expected_provider
        assert runner.last_provider == expected_provider
        assert runner.last_error is None
        assert providers_used == {expected_provider}
    calls = [json.loads(line) for line in calls_path.read_text(encoding='utf-8').splitlines()]
    assert [call[0] for call in calls] == expected_calls
    for name, args, prompt in calls:
        if name.endswith('generate'):
            assert json.loads(prompt.split('\n', 1)[1]) == {'task': 'probe', 'input': payload, 'repair': None}
            if name == 'codex generate':
                assert args[0] == 'exec' and args[-3:] == ['--model', 'codex-probe', '-']
            else:
                assert args[0] == '-p' and '--model' not in args
                assert json.loads(args[args.index('--json-schema')+1]) == Output.model_json_schema()


def test_timeout_kills_child_process(tmp_path):
    script = tmp_path/'parent.py'
    pid = tmp_path/'child.pid'
    script.write_text("import subprocess,sys,time,pathlib\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\npathlib.Path(sys.argv[1]).write_text(str(p.pid))\ntime.sleep(60)", encoding='utf-8')
    with pytest.raises(AppError) as error:
        run_bounded([sys.executable, str(script), str(pid)], input_bytes=b'', cwd=tmp_path, timeout=1)
    assert error.value.code == 'MODEL_TIMEOUT'
    assert pid.exists() and not psutil.pid_exists(int(pid.read_text()))


def test_ask_and_quota_validation():
    for data in ({'question': '  '}, {'question': 'x', 'mode': 'graph', 'allow_partial': True}):
        with pytest.raises(ValidationError):
            AskRequest(**data)
    with pytest.raises(AppError) as error:
        AgentRunner._failure('rate limit exceeded')
    assert error.value.code == 'MODEL_QUOTA_EXCEEDED'


@pytest.mark.integration
def test_worker_failure_retry_and_no_partial_index(db, settings, encoder, kb):
    doc = Document()
    doc.add_paragraph('张三负责凤凰项目。')
    buf = io.BytesIO()
    doc.save(buf)
    row = knowledge.upload_document(db, settings, kb['id'], 'retry.docx', buf.getvalue())['document']

    class Agent:
        stop = threading.Event()
        failed = True

        def generate_json(self, *args, **kwargs):
            if self.failed:
                raise AppError('MODEL_AUTH_REQUIRED', '请登录', 503)
            kwargs['on_provider']('claude')
            return GraphResult(relations=[])

    agent = Agent()
    worker = Worker(db, settings, encoder, agent)
    worker.process(worker.claim())
    with db.connect() as conn:
        failed = knowledge.document(conn, kb['id'], row['id'])
        assert (failed['status'], failed['stage'], failed['error_code']) == ('failed', 'graph', 'MODEL_AUTH_REQUIRED')
        assert not conn.execute('SELECT id FROM chunks WHERE document_id=%s', (row['id'],)).fetchone()
    assert knowledge.file_path(db, settings, kb['id'], row['id'])[0].is_file()
    retry = knowledge.retry_document(db, kb['id'], row['id'])
    assert retry['attempt'] == row['attempt']+1
    agent.failed = False
    worker.process(worker.claim())
    assert search(db, settings, encoder, kb['id'], SearchRequest(query='凤凰项目'))['results']
    with db.connect() as conn:
        stored = knowledge.document(conn, kb['id'], row['id'])['processing_meta']
        assert stored['provider'] == 'claude' and stored['providers_used'] == ['claude']


@pytest.mark.integration
def test_worker_recovery_and_exclusive_lock(db, settings, encoder, kb, monkeypatch):
    row, _, _ = prepare(db, settings, encoder, kb['id'], '重启测试。')
    from tests.conftest import FakeAgent
    # Hold the worker loop so recovery state can be inspected deterministically.
    monkeypatch.setattr(Worker, 'run', lambda self: self.stop.wait(10))
    first = Worker(db, settings, encoder, FakeAgent())
    second = Worker(db, settings, encoder, FakeAgent())
    try:
        first.start()
        with db.connect() as conn:
            current = knowledge.document(conn, kb['id'], row['id'])
            assert current['status'] == 'queued' and current['attempt'] == row['attempt']+1
        with pytest.raises(RuntimeError):
            second.start()
    finally:
        first.close()
        second.close()


@pytest.mark.integration
def test_stale_answer_rejected(db, settings, encoder, kb):
    doc, _ = publish(db, settings, encoder, kb['id'], '张三负责凤凰项目。')

    class Agent:
        def generate_json(self, *args, **kwargs):
            result = GeneratedAnswer(status='answered', claims=[{'text': '张三负责。', 'evidence_ids': ['E1']}], limitations=[])
            kwargs['validate'](result)
            knowledge.delete_document(db, settings, kb['id'], doc['id'])
            return result

    with pytest.raises(AppError) as error:
        answer(db, settings, encoder, Agent(), kb['id'], AskRequest(question='谁负责凤凰项目？'))
    assert error.value.code == 'EVIDENCE_STALE'


@pytest.mark.integration
def test_empty_evidence_and_invalid_citation(db, settings, encoder, kb):
    class Agent:
        calls = 0

        def generate_json(self, *args, **kwargs):
            self.calls += 1
            bad = GeneratedAnswer(status='answered', claims=[{'text': '不存在的来源', 'evidence_ids': ['E99']}], limitations=[])
            kwargs['validate'](bad)

    agent = Agent()
    result = answer(db, settings, encoder, agent, kb['id'], AskRequest(question='空库'))
    assert result['status'] == 'insufficient_evidence' and agent.calls == 0
    publish(db, settings, encoder, kb['id'], '张三负责凤凰项目。')
    with pytest.raises(ValueError):
        answer(db, settings, encoder, agent, kb['id'], AskRequest(question='凤凰项目'))


@pytest.mark.integration
def test_quote_validation_and_keyword_operators(db, settings, encoder, kb):
    _, chunks, _ = prepare(db, settings, encoder, kb['id'], '张三负责凤凰项目。')
    value = GraphResult.model_validate({'relations': [{'chunk_id': str(chunks[0]['id']),
        'subject': {'name': '张三', 'type': 'person'}, 'predicate': '负责',
        'object': {'name': '凤凰项目', 'type': 'project'}, 'quote': '虚构引用', 'occurrence': 0}]})
    with pytest.raises(ValueError):
        validate_relations(value, chunks)
    publish(db, settings, encoder, kb['id'], '张三负责凤凰项目。')
    for query in ['!!!', "' | & ! : ;", "'; DROP TABLE chunks;--"]:
        result = search(db, settings, encoder, kb['id'], SearchRequest(query=query, mode='keyword'))
        assert not result['results']


def test_streamlit_rerun_does_not_repeat_mutations(monkeypatch):
    import httpx
    from streamlit.testing.v1 import AppTest
    mutations = []
    kbs = []

    def request(method, url, **kwargs):
        path = url.split(':8000')[-1]
        if method == 'POST':
            mutations.append((path, kwargs.get('json')))
            kbs.append({'id': 'kb-a', 'name': '演示', 'description': '', 'revision': 0})
            data = kbs[-1]
        elif path == '/api/runtime':
            data = {'ready': True, 'provider': 'codex'}
        elif path.endswith('/documents'):
            data = {'items': [], 'total': 0}
        else:
            data = {'items': kbs, 'total': len(kbs)}
        return httpx.Response(200, json=data)

    monkeypatch.setenv('BACKEND_URL', 'http://127.0.0.1:8000')
    monkeypatch.setattr(httpx, 'request', request)
    app = AppTest.from_file(str(Path(__file__).parents[1]/'ui'/'streamlit_app.py')).run()
    assert not app.exception
    app.text_input[0].set_value('演示')
    app.button[0].click().run()
    assert len(mutations) == 1 and not app.exception
    app.run()
    app.selectbox(key='selected_kb').set_value('kb-a').run()
    assert len(mutations) == 1 and not app.exception
