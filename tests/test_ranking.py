import numpy as np
import pytest

from app.retrieval.search import search
from app.schemas import SearchRequest
from scripts.benchmark import score
from tests.test_integration import publish


@pytest.mark.integration
def test_postgres_vector_distance_orders_candidates(db, settings, encoder, kb):
    _, near = publish(db, settings, encoder, kb['id'], '相关文档。')
    _, far = publish(db, settings, encoder, kb['id'], '不相关文档。')
    orthogonal = np.zeros(512, dtype=np.float32)
    orthogonal[1] = 1
    with db.connect() as conn:
        conn.execute('UPDATE chunks SET embedding=%s WHERE id=%s', (orthogonal, far[0]['id']))
    result = search(db, settings, encoder, kb['id'], SearchRequest(query='问题', mode='vector'))
    assert result['results'][0]['chunk_id'] == str(near[0]['id'])
    assert result['results'][0]['scores']['vector_similarity'] == pytest.approx(1)
    assert result['results'][1]['scores']['vector_similarity'] == pytest.approx(0)


def test_benchmark_requires_both_hops():
    case = {'required_evidence_groups': [[{'file': 'a', 'quote': '负责'}], [{'file': 'b', 'quote': '使用'}]]}
    result = {'results': [{'document_name': 'a', 'text': '张三负责项目'}],
              'supporting_results': [{'document_name': 'b', 'text': '项目使用数据库'}]}
    actual = score(case, result)
    assert actual['primary_group_recall'] == .5
    assert actual['complete_group_recall'] == 1
    assert actual['all_groups_covered']
