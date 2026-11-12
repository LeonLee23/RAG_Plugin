"""Install only this project's skill in a selected client workspace."""
import argparse
import shutil
from pathlib import Path


def install(workspace, client):
    source = Path(__file__).resolve().parents[1]/'skills'/'knowledge-base'/'SKILL.md'
    destination = workspace.resolve()/('.agents' if client == 'codex' else '.claude')/'skills'/'knowledge-base'/'SKILL.md'
    if destination.exists() and destination.read_bytes() != source.read_bytes():
        raise SystemExit(f'目标已有不同内容，未覆盖：{destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    print(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--client', choices=['codex', 'claude'], required=True)
    args = parser.parse_args()
    install(args.workspace, args.client)
