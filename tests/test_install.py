import json
import tomllib

import pytest

from scripts.install_skill import install


def test_installer_both_clients_preserves_existing_settings(tmp_path):
    for client in ('codex', 'claude'):
        workspace = tmp_path / client
        workspace.mkdir()
        if client == 'codex':
            (workspace / '.codex').mkdir()
            (workspace / '.codex' / 'config.toml').write_text('model = "keep-this-model"\n', encoding='utf-8')
        else:
            (workspace / '.mcp.json').write_text('{"mcpServers":{"other":{"command":"other"}}}', encoding='utf-8')
        first = install(workspace, client, tmp_path / 'state', [tmp_path])
        assert install(workspace, client, tmp_path / 'state', [tmp_path]) == first
        if client == 'codex':
            value = tomllib.loads((workspace / '.codex' / 'config.toml').read_text(encoding='utf-8'))
            assert value['model'] == 'keep-this-model'
            assert value['mcp_servers']['ragplugin']['command']
        else:
            value = json.loads((workspace / '.mcp.json').read_text(encoding='utf-8'))
            assert value['mcpServers']['other']['command'] == 'other'
        with pytest.raises(ValueError, match='已有不同'):
            install(workspace, client, tmp_path / 'different', [tmp_path])
