"""Evidence-group retrieval evaluation, no LLM judge; answers are opt-in."""
import argparse
import json
import statistics
import time
from pathlib import Path

import httpx


def score(case, result, documents=None):
    primary = result['results']
    all_hits = primary+result['supporting_results']
    groups = case['required_evidence_groups']

    def matches(hit, expected):
        return (hit['document_name'] == expected['file'] and expected['quote'] in hit['text']
                and (documents is None or hit['document_id'] == documents[expected['file']]['id'])
                and (not expected.get('content_type') or hit['content_type'] == expected['content_type']))

    def coverage(hits):
        return sum(any(matches(hit, e) for e in group for hit in hits) for group in groups)/len(groups) if groups else None

    ranks = [i for i, hit in enumerate(primary, 1) if any(matches(hit, e) for group in groups for e in group)]
    return {'primary_group_recall': coverage(primary), 'complete_group_recall': coverage(all_hits),
            'all_groups_covered': coverage(all_hits) == 1 if groups else None,
            'mrr': 1/min(ranks) if ranks else (0 if groups else None),
            'retrieval_empty': not bool(all_hits)}


def benchmark(args):
    cases = [json.loads(line) for line in args.cases.read_text(encoding='utf-8').splitlines() if line.strip()]
    rows = []
    with httpx.Client(base_url=args.base_url, timeout=270, trust_env=False) as client:
        docs = []
        while True:
            response = client.get(f'/api/knowledge-bases/{args.kb_id}/documents',
                                  params={'limit': 100, 'offset': len(docs)})
            response.raise_for_status()
            page = response.json()
            docs.extend(page['items'])
            if len(docs) >= page['total'] or not page['items']:
                break
        documents = {}
        for filename in {e['file'] for c in cases for group in c['required_evidence_groups'] for e in group}:
            matched = [d for d in docs if d['filename'] == filename and d['status'] == 'ready']
            if len(matched) != 1:
                raise ValueError(f'评测文件必须对应唯一 ready 文档：{filename}')
            documents[filename] = {k: matched[0][k] for k in ('id', 'sha256')}
        for case in cases:
            for mode in ('vector', 'keyword', 'graph', 'hybrid'):
                started = time.monotonic()
                response = client.post(f'/api/knowledge-bases/{args.kb_id}/search', json={
                    'query': case['question'], 'mode': mode, 'top_k': args.top_k, 'debug': True})
                elapsed = (time.monotonic()-started)*1000
                response.raise_for_status()
                result = response.json()
                row = {'id': case['id'], 'category': case['category'], 'mode': mode, 'elapsed_ms': elapsed,
                       **score(case, result, documents), 'result': result}
                if args.answers and mode == 'hybrid':
                    response = client.post(f'/api/knowledge-bases/{args.kb_id}/ask', json={
                        'question': case['question'], 'mode': mode, 'top_k': args.top_k})
                    response.raise_for_status()
                    answer = response.json()
                    allowed = {s['evidence_id'] for s in answer['retrieval']['results']+answer['retrieval']['supporting_results']}
                    row['answer'] = answer
                    row['citation_ids_valid'] = all(set(c['evidence_ids']).issubset(allowed) for c in answer['claims'])
                    row['expected_answer_found'] = all(s in answer['answer'] for s in case['expected_answer_contains']) if case['answerable'] else None
                    row['correct_abstention'] = answer['status'] == 'insufficient_evidence' if not case['answerable'] else None
                rows.append(row)
            print(case['id'], flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'manifest.json').write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output/'results.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
    lines = ['# 检索评测结果', '', f'知识库：`{args.kb_id}`；问题数：{len(cases)}；top_k={args.top_k}。', '',
             '主结果和补充证据分别计分；图问题必须覆盖全部证据组。无答案题不计召回均值，检索返回相近文本不等于回答错误。', '',
             '| 模式 | 主证据组召回 | 含补充证据召回 | 全组覆盖率 | MRR | p50 / p95 ms |',
             '| --- | --- | --- | --- | --- | --- |']
    for mode in ('vector', 'keyword', 'graph', 'hybrid'):
        selected = [r for r in rows if r['mode'] == mode]
        answerable = [r for r in selected if r['complete_group_recall'] is not None]
        latency = sorted(r['elapsed_ms'] for r in selected)
        metrics = [statistics.mean(r[key] for r in answerable) for key in (
            'primary_group_recall', 'complete_group_recall', 'all_groups_covered', 'mrr')]
        lines.append(f"| {mode} | " + ' | '.join(f'{v:.3f}' for v in metrics) +
                     f" | {statistics.median(latency):.1f} / {latency[min(len(latency)-1, int(len(latency)*.95))]:.1f} |")
    lines += ['', '图片描述措辞会变化，标注只检查图片独有编号；这是一组小型自建数据，不能代表通用准确率。',
              '引用 ID 合法性不等于语义蕴含正确，仍需人工审阅回答。未加 --answers 时没有执行生成回答评测。',
              '优化时优先检查 results.jsonl 的失败问题与通道候选，再调整分块或实体归一；保留本次基线对照。']
    (args.output/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(args.output.resolve())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--kb-id', required=True)
    parser.add_argument('--cases', type=Path, default=Path('examples/eval.jsonl'))
    parser.add_argument('--top-k', type=int, choices=range(1, 21), default=5)
    parser.add_argument('--answers', action='store_true', help='Explicitly call real CLI once per case')
    parser.add_argument('--output', type=Path, default=Path('data/reports'))
    benchmark(parser.parse_args())
