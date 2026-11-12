def recall(conn, kb_id, vector, limit=20):
    rows = conn.execute("""SELECT c.id AS chunk_id,1-(c.embedding <=> %s::vector) AS score
        FROM chunks c JOIN documents d ON d.kb_id=c.kb_id AND d.id=c.document_id
        WHERE c.kb_id=%s AND d.status='ready'
        ORDER BY c.embedding <=> %s::vector,c.id LIMIT %s""", (vector, kb_id, vector, limit)).fetchall()
    return rows
