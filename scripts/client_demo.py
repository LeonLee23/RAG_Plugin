"""Explicit live Codex -> MCP demonstration, isolated from global client settings."""
import argparse
import json
import os
import tempfile
from pathlib import Path

from app.models import resolve_cli, run_bounded
from scripts.install_skill import install


def demonstrate(args):
    with tempfile.TemporaryDirectory(prefix='rag-client-') as folder:
        root = Path(folder)
        install(root, 'codex')
        # Task-local MCP configuration only. Internal AgentRunner continues to disable MCP.
        config = f'mcp_servers.rag-kb.url={json.dumps(args.base_url.rstrip("/")+"/mcp/")}'
        output = root/'answer.md'
        prompt = (f'使用 $knowledge-base 技能，通过 rag-kb MCP 先列出知识库，再检索 kb_id={args.kb_id}。'
                  f'问题：{args.question}。按技能要求引用文件名和位置。必须实际调用工具，不能凭记忆作答。')
        argv = resolve_cli('codex')+['exec', '--ephemeral', '--ignore-user-config', '--skip-git-repo-check',
            '--sandbox', 'read-only', '-c', config, '-c', 'features.shell_tool=false', '--json',
            '--output-last-message', str(output), '-']
        bypass = ','.join(filter(None, [os.getenv('NO_PROXY', ''), 'localhost', '127.0.0.1']))
        code, stdout, _ = run_bounded(argv, input_bytes=prompt.encode('utf-8'), cwd=root, timeout=300,
                                     extra_env={'NO_PROXY': bypass, 'no_proxy': bypass})
        if code or not output.exists():
            raise SystemExit('客户端演示失败，请检查 Codex 登录与 MCP 服务；未保存原始错误输出。')
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output/'client-answer.md').write_text(output.read_text(encoding='utf-8'), encoding='utf-8')
        calls = [event['item'] for event in events if event.get('type') == 'item.completed'
                 and event.get('item', {}).get('type') == 'mcp_tool_call']
        names = {call.get('tool') for call in calls}
        if not {'list_knowledge_bases', 'search_knowledge_base'}.issubset(names):
            print(json.dumps([{'event': e.get('type'), 'item_type': e.get('item', {}).get('type'),
                               'tool': e.get('item', {}).get('tool')} for e in events]))
            raise SystemExit('未观察到列库和检索两项实际 MCP 调用，不能判为通过。')
        (args.output/'client-tools.json').write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding='utf-8')
        print('已验证 Codex 实际调用列库和检索工具；答案与工具证据已保存。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--kb-id', required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--question', default='张三负责的项目使用什么数据库？')
    parser.add_argument('--output', type=Path, default=Path('data/reports'))
    demonstrate(parser.parse_args())
