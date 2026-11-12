import os

import httpx
import streamlit as st

BASE = os.getenv('BACKEND_URL', 'http://127.0.0.1:8000').rstrip('/')
st.set_page_config(page_title='RAG 知识库', layout='wide')


def api(method, path, *, raw=False, **kwargs):
    try:
        response = httpx.request(method, BASE+path, timeout=260 if path.endswith('/ask') else 30,
                                 trust_env=False, **kwargs)
        if response.is_error:
            try:
                error = response.json().get('error', {})
                st.error(f"{error.get('code', response.status_code)}：{error.get('message', '请求失败')}")
            except ValueError:
                st.error(f'服务请求失败：HTTP {response.status_code}')
            st.stop()
        return response.content if raw else (response.json() if response.content else None)
    except httpx.HTTPError:
        st.error('无法连接后端或请求超时。写操作不会自动重发，请刷新状态后再试。')
        st.stop()


def all_items(path):
    items = []
    while True:
        page = api('GET', path, params={'limit': 100, 'offset': len(items)})
        items.extend(page['items'])
        if len(items) >= page['total'] or not page['items']:
            return items


def show_evidence(result):
    if result.get('degraded') or result.get('truncated'):
        st.warning('结果不完整：' + '；'.join(result.get('warnings', [])))
    for group, label in [('results', '主证据'), ('supporting_results', '图路径补充证据')]:
        for item in result.get(group, []):
            title = f"{label} {item['evidence_id']} · {item['document_name']}"
            with st.expander(title):
                if item['content_type'] == 'image':
                    st.caption('以下文字由模型从图片提取，须结合原图核对。')
                st.text(item['text'])
                st.json({'位置': item['locations'], '召回': item['matched_by'], '分数': item['scores']})
                if item.get('asset_id'):
                    url = f"/api/knowledge-bases/{result['kb_id']}/documents/{item['document_id']}/assets/{item['asset_id']}"
                    if st.button('查看原图', key=f"img-{result['request_id']}-{item['evidence_id']}"):
                        st.image(api('GET', url, raw=True))
    if result.get('graph_paths'):
        st.json({'关系路径': result['graph_paths']})
    if not result.get('results'):
        st.info('未找到证据：' + str(result.get('empty_reason', 'no_matches')))
    if result.get('diagnostics'):
        st.json(result['diagnostics'])


st.title('RAG 知识库')
runtime = api('GET', '/api/runtime')
with st.sidebar:
    embedding = runtime.get('embedding_model', '远程') if runtime.get('embedding_ready') else '待配置'
    providers = ' → '.join(runtime.get('providers') or [runtime['provider']])
    st.caption(f"执行器优先顺序：{providers} · 向量服务：{embedding}")
    if provider := runtime.get('last_model_provider'):
        st.caption(f"上次成功执行：{provider}")
    counts = runtime.get('document_counts', {})
    st.caption(f"排队 {counts.get('queued', 0)} · 处理 {counts.get('processing', 0)} · 失败 {counts.get('failed', 0)}")
    if runtime.get('last_model_error'):
        st.warning(runtime['last_model_error'])
if not runtime['ready']:
    st.warning(runtime.get('error') or '后端正在初始化。')
    if st.button('刷新运行状态'):
        st.rerun()
    st.stop()

kbs = all_items('/api/knowledge-bases')
with st.sidebar:
    with st.form('create-kb'):
        name = st.text_input('新知识库名称', max_chars=100)
        description = st.text_area('简介', max_chars=1000)
        if st.form_submit_button('创建知识库'):
            api('POST', '/api/knowledge-bases', json={'name': name, 'description': description})
            st.rerun()
    if not kbs:
        st.info('先创建一个知识库。')
        st.stop()
    by_id = {k['id']: k for k in kbs}
    kb_id = st.selectbox('当前知识库', list(by_id), format_func=lambda key: by_id[key]['name'], key='selected_kb')
    kb = by_id[kb_id]
    st.caption(kb['description'])
    confirmed = st.checkbox(f"确认删除「{kb['name']}」及全部文档", key=f'confirm-{kb_id}')
    if st.button('删除知识库', disabled=not confirmed):
        api('DELETE', f'/api/knowledge-bases/{kb_id}')
        st.session_state.pop(f'history-{kb_id}', None)
        st.rerun()

prefix = f'/api/knowledge-bases/{kb_id}'
result_key = f'result-{kb_id}'
old = st.session_state.get(result_key)
if old and old['kb_revision'] != kb['revision']:
    st.session_state.pop(result_key)

documents_tab, search_tab, chat_tab = st.tabs(['文档管理', '检索调试', '证据问答'])
with documents_tab:
    with st.form(f'upload-{kb_id}'):
        uploaded = st.file_uploader('上传 PDF / DOCX（最多 20 MiB）', type=['pdf', 'docx'])
        if st.form_submit_button('上传并建立索引'):
            if uploaded:
                result = api('POST', prefix+'/documents', files={'file': (uploaded.name, uploaded.getvalue())})
                st.success('文件已存在' if result['deduplicated'] else '已进入处理队列')
            else:
                st.warning('请先选择文件。')
    if st.button('刷新处理状态'):
        st.rerun()
    docs = all_items(prefix+'/documents')
    st.dataframe([{k: d.get(k) for k in ('filename', 'status', 'stage', 'progress', 'error_code', 'error_message')}
                  for d in docs], width='stretch')
    if docs:
        doc_map = {d['id']: d for d in docs}
        doc_id = st.selectbox('查看文档', list(doc_map), format_func=lambda x: doc_map[x]['filename'])
        selected = doc_map[doc_id]
        detail = prefix+f'/documents/{doc_id}'
        st.download_button('下载原文件', api('GET', detail+'/file', raw=True), file_name=selected['filename'])
        if selected['status'] == 'failed' and st.button('重试处理'):
            api('POST', detail+'/retry')
            st.rerun()
        if st.button('删除所选文档'):
            api('DELETE', detail)
            st.session_state.pop(result_key, None)
            st.rerun()

with search_tab:
    with st.form(f'search-{kb_id}'):
        query = st.text_input('检索问题', max_chars=2000)
        mode = st.selectbox('召回方式', ['hybrid', 'vector', 'keyword', 'graph'])
        top_k = st.number_input('主结果数量', min_value=1, max_value=20, value=5)
        if st.form_submit_button('检索'):
            st.session_state[result_key] = api('POST', prefix+'/search', json={
                'query': query, 'mode': mode, 'top_k': int(top_k), 'debug': True})
    if result_key in st.session_state:
        show_evidence(st.session_state[result_key])

with chat_tab:
    history = st.session_state.setdefault(f'history-{kb_id}', [])
    for entry in history:
        with st.chat_message('user'):
            st.text(entry['question'])
        with st.chat_message('assistant'):
            st.text(entry['answer']['answer'])
            if entry['answer']['retrieval']['kb_revision'] != kb['revision']:
                st.caption('历史回答：知识库已变化，请重新提问获取当前证据。')
            else:
                show_evidence(entry['answer']['retrieval'])
    if question := st.chat_input('根据当前知识库提问（每次问题独立检索）', max_chars=2000):
        with st.spinner('检索并生成带引用的回答…'):
            result = api('POST', prefix+'/ask', json={'question': question})
        history.append({'question': question, 'answer': result})
        st.rerun()
