import json
import time
from uuid import uuid4

import psycopg

from app.db import get_kb
from app.errors import AppError
from app.knowledge import profile
from app.retrieval import graph, keyword, vector
from app.text import terms


def merge_rankings(channels):
    merged = {}
    for mode, candidates in channels.items():
        seen = set()
        for rank, hit in enumerate(candidates, 1):
            cid = hit['chunk_id']
            if cid in seen:
                continue
            seen.add(cid)
            item = merged.setdefault(cid, {"chunk_id": cid, "ranks": {}, "scores": {}, "rrf": 0.0})
            item['ranks'][mode] = rank
            item['scores'][mode] = float(hit['score'])
            item['rrf'] += 1/(60+rank)
    return sorted(merged.values(), key=lambda h: (-h['rrf'], -len(h['ranks']),
                   min(h['ranks'].values()), str(h['chunk_id'])))


def search(db, settings, encoder, kb_id, request, request_id=None):
    started = time.monotonic()
    request_id = request_id or str(uuid4())
    with db.connect() as conn:
        kb = get_kb(conn, kb_id)
        if request.mode in ('vector', 'hybrid') and kb['embedding_profile'] != profile(settings):
            raise AppError("EMBEDDING_PROFILE_MISMATCH", "请在当前模型配置下新建知识库并重新上传", 409)
    query_vector, encoding_error = None, None
    if request.mode in ('vector', 'hybrid'):
        try:
            query_vector = encoder.encode([request.query], kind='query')[0]
        except AppError as exc:
            if not (request.mode == 'hybrid' and request.allow_partial):
                raise
            encoding_error = exc
    channels, failures, timings = {}, [], {}
    paths, graph_info = [], {}
    modes = ('vector', 'keyword', 'graph') if request.mode == 'hybrid' else (request.mode,)
    with db.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '5s'")
        kb = get_kb(conn, kb_id)
        ready_count = conn.execute("SELECT count(*) AS n FROM documents WHERE kb_id=%s AND status='ready'",
                                  (kb_id,)).fetchone()['n']
        for mode in modes:
            t = time.monotonic()
            try:
                # A failing channel rolls back its savepoint, preserving the shared snapshot.
                with conn.transaction():
                    if mode == 'vector':
                        if encoding_error:
                            raise encoding_error
                        channels[mode] = vector.recall(conn, kb_id, query_vector)
                    elif mode == 'keyword':
                        channels[mode] = keyword.recall(conn, kb_id, request.query)
                    else:
                        channels[mode], paths, graph_info = graph.recall(conn, kb_id, request.query)
            except (AppError, psycopg.Error) as exc:
                if conn.closed or not (request.mode == 'hybrid' and request.allow_partial):
                    if isinstance(exc, AppError):
                        raise
                    raise AppError("RETRIEVAL_FAILED", f"{mode} 检索失败", 503, True) from exc
                failures.append(mode)
            timings[mode] = round((time.monotonic()-t)*1000, 2)
        if not channels:
            raise AppError("RETRIEVAL_FAILED", "全部检索通道失败", 503, True)
        ranked = merge_rankings(channels)
        if request.mode != 'hybrid':
            ranked.sort(key=lambda h: h['ranks'][request.mode])
        wanted = {h['chunk_id'] for h in ranked}
        wanted.update(e['chunk_id'] for path in paths for e in path)
        rows = conn.execute("""SELECT c.*,d.filename FROM chunks c JOIN documents d
            ON d.kb_id=c.kb_id AND d.id=c.document_id
            WHERE c.kb_id=%s AND d.status='ready' AND c.id=ANY(%s)""", (kb_id, list(wanted))).fetchall()
        by_id = {r['id']: r for r in rows}
        primary, selected, chosen_paths = [], [], []
        truncated = bool(graph_info.get('graph_truncated'))
        total_chars = 0
        for hit in ranked:
            cid = hit['chunk_id']
            if len(primary) >= request.top_k:
                break
            path = next((p for p in paths if any(e['chunk_id'] == cid for e in p)), [])
            group = list(dict.fromkeys([cid] + [e['chunk_id'] for e in path]))
            new = [v for v in group if v not in selected]
            if any(v not in by_id for v in group):
                continue
            cost = sum(len(by_id[v]['evidence_text']) for v in new)
            if len(selected)+len(new) > 20 or total_chars+cost > 30000:
                truncated = True
                continue
            primary.append(cid)
            selected.extend(new)
            total_chars += cost
            if path and path not in chosen_paths:
                chosen_paths.append(path)
        support = [cid for cid in selected if cid not in primary]
        evidence_ids = {cid: f"E{i+1}" for i, cid in enumerate(primary+support)}
        hits = {h['chunk_id']: h for h in ranked}

        def evidence(cid):
            row = by_id[cid]
            hit = hits.get(cid, {"ranks": {}, "scores": {}, "rrf": None})
            locations, loc_seen = [], set()
            for span in row['source_spans']:
                key = json.dumps(span['location'], sort_keys=True)
                if key not in loc_seen:
                    locations.append(span['location'])
                    loc_seen.add(key)
            return {"evidence_id": evidence_ids[cid], "chunk_id": str(cid),
                "document_id": str(row['document_id']), "document_name": row['filename'],
                "content_type": row['content_type'], "text": row['evidence_text'], "locations": locations,
                "asset_id": row['asset_id'], "matched_by": list(hit['ranks']), "ranks": hit['ranks'],
                "scores": {"vector_similarity": hit['scores'].get('vector'),
                           "keyword_rank": hit['scores'].get('keyword'),
                           "rrf": hit['rrf'] if request.mode == 'hybrid' else None}}

        graph_paths = [{"edges": [{**{k: str(v) if k.endswith('_id') else v for k, v in edge.items()
                                    if k != 'chunk_id'}, "evidence_id": evidence_ids[edge['chunk_id']]}
                                  for edge in p]} for p in chosen_paths]
        reason = None
        if not primary:
            reason = 'no_matches'
            if not ready_count:
                reason = 'no_ready_documents'
            elif request.mode == 'keyword' and not terms(request.query, query=True):
                reason = 'no_query_terms'
            elif request.mode == 'graph':
                reason = 'no_seed_entity' if not graph_info.get('seed_count') else 'no_graph_path'
        response = {"request_id": request_id, "kb_id": str(kb_id), "kb_revision": kb['revision'],
            "mode": request.mode, "degraded": bool(failures), "failed_modes": failures,
            "truncated": truncated, "empty_reason": reason, "results": [evidence(c) for c in primary],
            "supporting_results": [evidence(c) for c in support], "graph_paths": graph_paths,
            "warnings": (["部分检索通道失败"] if failures else []) + (["结果或图遍历已截断"] if truncated else [])}
    if request.debug:
        response['diagnostics'] = {"elapsed_ms": round((time.monotonic()-started)*1000, 2),
            "channel_ms": timings, "candidate_counts": {k: len(v) for k, v in channels.items()}, **graph_info}
    return response
