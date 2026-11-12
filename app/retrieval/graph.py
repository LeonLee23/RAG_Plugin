import re

from app.text import normalize, terms


def mentions(query, name):
    if re.search(r'[\u3400-\u9fff]', name):
        return name in query
    return re.search(r'(?<![\w])' + re.escape(name) + r'(?![\w])', query) is not None


def recall(conn, kb_id, query, limit=20):
    normalized = normalize(query)
    entities = conn.execute("""SELECT e.* FROM entities e WHERE e.kb_id=%s AND EXISTS
        (SELECT 1 FROM relations r JOIN chunks c ON c.kb_id=r.kb_id AND c.id=r.evidence_chunk_id
         JOIN documents d ON d.kb_id=c.kb_id AND d.id=c.document_id
         WHERE r.kb_id=e.kb_id AND d.status='ready'
           AND (r.source_entity_id=e.id OR r.target_entity_id=e.id))""", (kb_id,)).fetchall()
    seeds = sorted([e for e in entities if mentions(normalized, e['normalized_name'])],
                   key=lambda e: (-len(e['normalized_name']), e['normalized_name'], str(e['id'])))[:5]
    words = set(terms(query, query=True)) - {e['normalized_name'] for e in seeds}
    paths, seen, cache = [], set(), {}
    truncated = False

    def score(path):
        text = ' '.join(e['source']+' '+e['predicate']+' '+e['target'] for e in path)
        return len(words & set(terms(text)))

    def neighbors(entity_id):
        nonlocal truncated
        if entity_id not in cache:
            rows = conn.execute("""SELECT r.id AS relation_id,r.source_entity_id,r.target_entity_id,
                r.predicate,r.evidence_chunk_id AS chunk_id,r.quote,r.quote_start,r.quote_end,
                s.name AS source,t.name AS target FROM relations r
                JOIN entities s ON s.kb_id=r.kb_id AND s.id=r.source_entity_id
                JOIN entities t ON t.kb_id=r.kb_id AND t.id=r.target_entity_id
                JOIN chunks c ON c.kb_id=r.kb_id AND c.id=r.evidence_chunk_id
                JOIN documents d ON d.kb_id=c.kb_id AND d.id=c.document_id
                WHERE r.kb_id=%s AND d.status='ready'
                  AND (r.source_entity_id=%s OR r.target_entity_id=%s)""",
                (kb_id, entity_id, entity_id)).fetchall()
            rows.sort(key=lambda e: (-score([e]), str(e['relation_id'])))
            truncated |= len(rows) > 20
            cache[entity_id] = rows[:20]
        return cache[entity_id]

    for seed in seeds:
        frontier = [(seed['id'], {seed['id']}, [])]
        for _ in range(2):
            next_frontier = []
            for node, visited, path in frontier:
                for edge in neighbors(node):
                    other = edge['target_entity_id'] if edge['source_entity_id'] == node else edge['source_entity_id']
                    if other in visited:
                        continue
                    next_path = path + [edge]
                    key = tuple(e['relation_id'] for e in next_path)
                    if key not in seen:
                        seen.add(key)
                        paths.append(next_path)
                    next_frontier.append((other, visited | {other}, next_path))
            frontier = next_frontier
    paths.sort(key=lambda p: (-score(p), len(p), tuple(str(e['relation_id']) for e in p)))
    truncated |= len(paths) > 100
    paths = paths[:100]
    candidates, chunk_ids = [], set()
    for path in paths:
        for edge in path:
            if edge['chunk_id'] not in chunk_ids:
                chunk_ids.add(edge['chunk_id'])
                candidates.append({"chunk_id": edge['chunk_id'], "score": score(path)})
    return candidates[:limit], paths, {"seed_count": len(seeds), "graph_truncated": truncated}
