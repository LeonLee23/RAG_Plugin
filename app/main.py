"""Run with python -m app.main. stdout is exclusively MCP protocol."""
import argparse
import logging
import os
from pathlib import Path

from app.config import Settings
from app.mcp import create_mcp


def main():
    parser = argparse.ArgumentParser(description='RAGPlugin local stdio MCP')
    parser.add_argument('--state-dir', type=Path,
                        default=Path(os.environ.get('RAGPLUGIN_STATE_DIR', str(Path.home() / '.ragplugin'))))
    parser.add_argument('--allow-root', action='append', type=Path,
                        help='Allowed source/package root; repeat for additional roots')
    args = parser.parse_args()
    roots = args.allow_root or [Path(p) for p in os.environ.get('RAGPLUGIN_ALLOWED_ROOTS', '').split(os.pathsep) if p]
    if not roots:
        parser.error('Specify --allow-root or RAGPLUGIN_ALLOWED_ROOTS; no implicit disk access')
    settings = Settings(args.state_dir.absolute(), tuple(p.absolute() for p in roots))
    logging.basicConfig(level=logging.WARNING)
    create_mcp(settings).run(transport='stdio')


if __name__ == '__main__':
    main()
