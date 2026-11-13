"""Read-only V2 lexical candidate/evidence evaluation; never calls a model."""
import argparse
import json
import time
from pathlib import Path

from app.config import Settings
from app.retrieval import search


def evaluate(settings, kb_id, cases, limit):
    results = []
    for case in cases:
        started = time.perf_counter()
        result = search.search_knowledge_base(settings, kb_id, case['question'], limit=limit)
        evidence = []
        for hit in result['items']:
            if hit['asset_id']:
                ev = search.read_asset(settings, kb_id, hit['doc_id'], hit['doc_revision'], hit['asset_id'],
                                       expected_kb_revision=result['kb_revision'])
                evidence.append(ev)
            else:
                cursor = None
                while True:
                    ev = search.read_evidence(settings, kb_id, hit['doc_id'], hit['doc_revision'], hit['node_id'],
                                              cursor=cursor, expected_kb_revision=result['kb_revision'])
                    evidence.append(ev)
                    cursor = ev['next_cursor']
                    if not cursor:
                        break
        groups = case['required_evidence_groups']
        covered = sum(any(ev['name'] == expected['file'] and expected['quote'] in ev['text']
                          and (not expected.get('content_type') or ev['evidence_type'] ==
                               {'text': 'original', 'image': 'image_description'}[expected['content_type']])
                          for expected in group for ev in evidence) for group in groups)
        results.append({'id': case['id'], 'category': case['category'], 'candidates': len(result['items']),
                        'total_candidates': result['total'], 'truncated': result['truncated'],
                        'evidence_group_coverage': covered / len(groups) if groups else None,
                        'seconds': time.perf_counter() - started, 'answer_quality': 'not_measured'})
    return {'version': 'V2', 'method': 'lexical_candidates_then_full_selected_nodes',
            'host_tree_reasoning': 'not_measured', 'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--kb-id', required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--allow-root', type=Path, action='append', required=True)
    parser.add_argument('--dataset', type=Path, default=Path('examples/eval.jsonl'))
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--output', type=Path, default=Path('data/reports/v2-lexical.json'))
    args = parser.parse_args()
    cases = [json.loads(line) for line in args.dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
    report = evaluate(Settings(args.state_dir, tuple(args.allow_root)), args.kb_id, cases, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(args.output.absolute())
