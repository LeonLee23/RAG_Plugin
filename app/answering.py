import time

from app.db import get_kb
from app.errors import AppError
from app.retrieval.search import search
from app.schemas import GeneratedAnswer


def answer(db, settings, encoder, agent, kb_id, request, request_id=None):
    deadline = time.monotonic() + 240
    evidence = search(db, settings, encoder, kb_id, request.search_request(), request_id)
    sources = evidence['results'] + evidence['supporting_results']
    if not sources:
        return {"status": "insufficient_evidence", "claims": [], "limitations": ["未找到相关证据"],
                "answer": "知识库中未找到可支持该问题的证据。", "retrieval": evidence}
    ids = {s['evidence_id'] for s in sources}

    def validate(result):
        for claim in result.claims:
            if not set(claim.evidence_ids).issubset(ids):
                raise ValueError("Unknown evidence ID")

    result = agent.generate_json("仅根据证据回答问题，每个结论引用有效证据ID。无答案返回 insufficient_evidence；"
        "冲突返回 conflicting_evidence；两跳结论必须引用各边证据。不要生成文件名或URL。",
        {"question": request.question, "evidence": sources, "graph_paths": evidence['graph_paths']},
        GeneratedAnswer, validate=validate, deadline=deadline)
    with db.connect() as conn:
        if get_kb(conn, kb_id)['revision'] != evidence['kb_revision']:
            raise AppError("EVIDENCE_STALE", "回答期间知识库已变化，请重新检索", 409, True)
    text = '\n'.join(c.text + ' [' + ', '.join(c.evidence_ids) + ']' for c in result.claims)
    if not text:
        text = "现有证据不足以回答该问题。"
    return {**result.model_dump(), "answer": text, "retrieval": evidence}
