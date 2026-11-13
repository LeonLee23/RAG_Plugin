"""Install the shared skill and project-scoped stdio configuration, never global config."""
import argparse
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path
from uuid import uuid4

from app.storage import no_links

REPO = Path(__file__).resolve().parents[1]


def server_config(state_dir, roots):
    args = ['-m', 'app.main', '--state-dir', str(state_dir)]
    for root in roots:
        args += ['--allow-root', str(root)]
    return {'command': sys.executable, 'args': args, 'cwd': str(REPO)}


def install(workspace, client, state_dir=None, roots=None):
    workspace = no_links(workspace)
    roots = [no_links(p) for p in (roots or [workspace])]
    state_dir = no_links(state_dir or Path.home() / '.ragplugin')
    config = server_config(state_dir, roots)
    source = REPO / 'skills' / 'ragplugin' / 'SKILL.md'
    skill = no_links(workspace / ('.agents' if client == 'codex' else '.claude') / 'skills' / 'ragplugin' / 'SKILL.md')
    if skill.exists() and skill.read_bytes() != source.read_bytes():
        raise ValueError(f'目标 Skill 已有不同内容，未覆盖：{skill}')
    if client == 'codex':
        destination = no_links(workspace / '.codex' / 'config.toml')
        previous = destination.read_text(encoding='utf-8') if destination.exists() else ''
        parsed = tomllib.loads(previous)
        old = parsed.get('mcp_servers', {}).get('ragplugin')
        target = {**config, 'startup_timeout_sec': 30, 'tool_timeout_sec': 120}
        if old is not None and old != target:
            raise ValueError(f'已有不同 ragplugin 配置，未覆盖：{destination}')
        addition = '\n[mcp_servers.ragplugin]\n' + '\n'.join(
            f'{key} = {json.dumps(value, ensure_ascii=False)}' for key, value in target.items()) + '\n'
        content = previous if old is not None else previous + addition
        tomllib.loads(content)
    else:
        destination = no_links(workspace / '.mcp.json')
        data = json.loads(destination.read_text(encoding='utf-8')) if destination.exists() else {'mcpServers': {}}
        # Claude's MCP config does not use cwd; uv --directory supplies the same working directory.
        uv = shutil.which('uv')
        if not uv:
            raise ValueError('uv 未在 PATH 中')
        target = {'type': 'stdio', 'command': uv,
                  'args': ['--directory', str(REPO), 'run', '--no-sync', '--frozen', 'python', *config['args']]}
        old = data.setdefault('mcpServers', {}).get('ragplugin')
        if old is not None and old != target:
            raise ValueError(f'已有不同 ragplugin 配置，未覆盖：{destination}')
        data['mcpServers']['ragplugin'] = target
        content = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    # Check both destinations before changing either; preserve unrelated settings.
    skill.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not skill.exists():
        skill.write_bytes(source.read_bytes())
    temporary = destination.with_name(destination.name + f'.ragplugin-{uuid4().hex}.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {'skill': str(skill), 'config': str(destination), 'state_dir': str(state_dir),
            'allowed_roots': [str(p) for p in roots]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--client', choices=['codex', 'claude'], required=True)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--allow-root', action='append', type=Path)
    args = parser.parse_args()
    print(json.dumps(install(args.workspace, args.client, args.state_dir, args.allow_root),
                     ensure_ascii=False, indent=2))
