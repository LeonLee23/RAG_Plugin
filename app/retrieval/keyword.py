from psycopg import sql

from app.text import terms


def recall(conn, kb_id, query, limit=20):
    words = terms(query, query=True)
    if not words:
        return []
    # Only SQL structure is assembled; query words always remain bound parameters.
    expression = sql.SQL(' || ').join(sql.SQL("plainto_tsquery('simple', %s)") for _ in words)
    statement = sql.SQL("""WITH q AS (SELECT {expression} AS query)
        SELECT c.id AS chunk_id, ts_rank_cd(c.search_vector,q.query) AS score
        FROM chunks c JOIN documents d ON d.kb_id=c.kb_id AND d.id=c.document_id CROSS JOIN q
        WHERE c.kb_id=%s AND d.status='ready' AND c.search_vector @@ q.query
        ORDER BY score DESC,c.id LIMIT %s""").format(expression=expression)
    return conn.execute(statement, (*words, kb_id, limit)).fetchall()
