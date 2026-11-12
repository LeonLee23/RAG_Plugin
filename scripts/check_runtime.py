"""Safe environment report. --live-model explicitly sends only synthetic probe data."""
import argparse
import json
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.db import Database
from app.embeddings import DIMENSION, Embeddings
from app.errors import AppError
from app.models import AgentRunner, resolve_cli, run_bounded


class Probe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: str


def check(live_model=False, skip_embedding=False):
    settings = Settings()
    results = {}
    cli_checks = {}
    for provider in settings.agent_providers:
        try:
            command = resolve_cli(provider)
            code, stdout, _ = run_bounded(command+['--version'], input_bytes=b'', cwd=Path.cwd(), timeout=10)
            cli_checks[provider] = {'ok': code == 0, 'version': stdout.strip()}
        except AppError as exc:
            cli_checks[provider] = {'ok': False, 'error': exc.code}
    results['cli'] = {'ok': any(r['ok'] for r in cli_checks.values()), 'providers': cli_checks}
    try:
        with Database(settings.database_url).connect() as conn:
            ext = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
            results['database'] = {'ok': bool(ext), 'pgvector': ext['extversion'] if ext else None}
    except Exception as exc:
        results['database'] = {'ok': False, 'error': type(exc).__name__}
    if not skip_embedding:
        try:
            encoder = Embeddings(settings)
            encoder.load()
            if live_model:
                vectors = encoder.encode(['凤凰项目使用星河数据库。'])
                norm = float((vectors[0]**2).sum()**0.5)
                results['embedding'] = {'ok': vectors.shape == (1, DIMENSION) and abs(norm-1) < .001,
                                        'shape': list(vectors.shape), 'norm': norm}
            else:
                results['embedding'] = {'ok': encoder.endpoint is not None, 'checked': 'configuration'}
        except AppError as exc:
            results['embedding'] = {'ok': False, 'error': exc.code, 'message': exc.message}
        except Exception as exc:
            results['embedding'] = {'ok': False, 'error': type(exc).__name__}
    if live_model:
        runner = AgentRunner(settings)
        for kind in ('text', 'vision'):
            try:
                if kind == 'text':
                    result = runner.generate_json('将 value 原样返回为 ok', {}, Probe)
                    results[kind] = {'ok': result.value == 'ok'}
                else:
                    with tempfile.TemporaryDirectory() as folder:
                        path = Path(folder)/'probe.png'
                        image = Image.new('RGB', (480, 160), 'white')
                        ImageDraw.Draw(image).text((35, 55), 'RAG-42', fill='black', font_size=48)
                        image.save(path)
                        result = runner.describe_image(path)
                    results[kind] = {'ok': 'RAG-42' in result.model_dump_json()}
                results[kind]['provider'] = runner.last_provider
            except AppError as exc:
                results[kind] = {'ok': False, 'error': exc.code}
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return all(r['ok'] for r in results.values())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live-model', action='store_true')
    parser.add_argument('--skip-embedding', action='store_true')
    args = parser.parse_args()
    raise SystemExit(0 if check(args.live_model, args.skip_embedding) else 1)
