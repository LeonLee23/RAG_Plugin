import numpy as np
import pytest
from fastapi.testclient import TestClient

from scripts.serve_embeddings import MODEL_NAMES, create_app


class ProbeModel:
    max_seq_length = 16

    def __init__(self):
        self.inputs = []

    def tokenizer(self, texts, **kwargs):
        assert kwargs == {"truncation": False, "padding": False}
        return {"input_ids": [list(range(len(text))) for text in texts]}

    def encode(self, texts, **kwargs):
        self.inputs.append(texts)
        assert kwargs["normalize_embeddings"]
        vectors = np.zeros((len(texts), 1792))
        vectors[:, 0] = 1  # Protocol fixture only, never production embeddings.
        return vectors


def test_embedding_server_auth_validation_and_batch_order():
    model = ProbeModel()
    client = TestClient(create_app(model, "test-secret"))
    body = {"model": MODEL_NAMES[0], "input": ["凤凰", "星河"]}
    assert client.get("/health").status_code == 200
    assert client.post("/v1/embeddings", json=body).status_code == 401
    client.headers["Authorization"] = "Bearer incorrect"
    assert client.get("/v1/models").status_code == 401
    client.headers["Authorization"] = "Bearer test-secret"
    assert [x["id"] for x in client.get("/v1/models").json()["data"]] == list(MODEL_NAMES)
    assert client.post("/v1/embeddings", json={**body, "model": "unknown"}).status_code == 404
    for texts in ([], [" "], ["x" * 17], ["x"] * 33):
        assert client.post("/v1/embeddings", json={**body, "input": texts}).status_code == 422
    assert model.inputs == []
    response = client.post("/v1/embeddings", json=body)
    assert response.status_code == 200
    assert [x["index"] for x in response.json()["data"]] == [0, 1]
    assert all(len(x["embedding"]) == 1792 for x in response.json()["data"])
    assert model.inputs == [body["input"]]
    with pytest.raises(ValueError, match="EMBEDDING_API_KEY"):
        create_app(model, "")
