"""Offline stdio installation diagnostic. Does not call a model or touch user packages."""
import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe():
    with tempfile.TemporaryDirectory(prefix='ragplugin-diagnostic-') as folder:
        root = Path(folder)
        params = StdioServerParameters(command=sys.executable,
            args=['-m', 'app.main', '--state-dir', str(root / 'state'), '--allow-root', str(root)],
            cwd=str(Path(__file__).resolve().parents[1]))
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
            initialized = await client.initialize()
            tools = (await client.list_tools()).tools
            result = await client.call_tool('list_knowledge_bases', {})
            assert not result.isError
            assert len(tools) == 17 and sum(bool(t.annotations.readOnlyHint) for t in tools) == 9
            invalid = await client.call_tool('search_knowledge_base', {'kb_id': '0' * 32, 'query': 'probe'})
            assert invalid.isError and json.loads(invalid.content[0].text)['error']['code'] == 'KB_NOT_FOUND'
            return {'protocol': initialized.protocolVersion, 'server': initialized.serverInfo.model_dump(),
                    'tools': len(tools), 'read_only_tools': 9, 'management_tools': 8, 'stdio': 'passed',
                    'model_called': False, 'host_acceptance': 'not_tested'}


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(asyncio.run(probe()), ensure_ascii=False, indent=2))
