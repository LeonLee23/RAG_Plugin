import json

import httpx
import numpy as np
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.embeddings import QUERY_INSTRUCTION, Embeddings
from app.errors import AppError


def test_blank_embedding_url_disables_encoding_without_network(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BASE_URL", "")
    encoder = Embeddings(Settings(_env_file=None))
    encoder.load()
    assert encoder.endpoint is None
    with pytest.raises(AppError, match="请在 .env 中设置 EMBEDDING_BASE_URL"):
        encoder.encode(["凤凰项目"])


@pytest.fixture
def remote_encoder():
    encoder = Embeddings(Settings(
        embedding_base_url="http://embedding.example.com/api/v1/",
        embedding_model="bge-deployment",
        _env_file=None,
    ))
    encoder.load()
    return encoder


@pytest.mark.parametrize("width", [512, 1792])
@pytest.mark.parametrize("api_key", ["", "test-embedding-secret"])
def test_remote_batch_order_and_query_instruction(remote_encoder, monkeypatch, width, api_key):
    remote_encoder.settings.embedding_api_key = SecretStr(api_key)
    calls = []

    def respond(request):
        assert request.method == "POST"
        assert request.url == "http://embedding.example.com/api/v1/embeddings"
        assert request.headers.get("Authorization") == (f"Bearer {api_key}" if api_key else None)
        payload = json.loads(request.content)
        assert payload["model"] == "bge-deployment"
        calls.append(payload["input"])
        return httpx.Response(200, json={"data": [
            {"index": index, "embedding": [index * 3, 2] + [0] * 510 + [10] * (width - 512)}
            for index in reversed(range(len(payload["input"])))
        ]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        vectors = remote_encoder.encode(["凤凰项目", "星河数据库"])
        remote_encoder.encode(["谁负责凤凰项目？"], kind="query")
        monkeypatch.setattr("app.embeddings.MAX_INPUT_TOKENS", 64)
        with pytest.raises(AppError, match="超过 QZhou"):
            remote_encoder.encode(["凤" * 65])

    assert calls == [
        ["This sentence: <|im_start|>“凤凰项目” means in one word: “",
         "This sentence: <|im_start|>“星河数据库” means in one word: “"],
        [f"This sentence: <|im_start|>“{QUERY_INSTRUCTION}谁负责凤凰项目？” means in one word: “"],
    ]
    assert vectors.shape == (2, 512) and vectors.dtype == np.float32
    assert vectors[:, 0] == pytest.approx([0, 3 / np.sqrt(13)])
    assert np.linalg.norm(vectors, axis=1) == pytest.approx([1, 1])


@pytest.mark.parametrize("status", [401, 403])
def test_auth_error_explains_configuration_without_exposing_key(remote_encoder, monkeypatch, status):
    key = "test-embedding-secret"
    monkeypatch.setenv("EMBEDDING_API_KEY", key)
    settings = Settings(_env_file=None)
    assert key not in repr(settings)
    assert key not in settings.model_dump_json()
    remote_encoder.settings.embedding_api_key = settings.embedding_api_key
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(status, json={"error": key})
    )) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        with pytest.raises(AppError, match="EMBEDDING_API_KEY") as error:
            remote_encoder.encode(["凤凰项目"])
    assert str(status) in error.value.message
    assert key not in error.value.message
    assert not error.value.retryable


@pytest.mark.parametrize(("items", "code"), [
    ([], "EMBEDDING_UNAVAILABLE"),
    ([{"index": 0, "embedding": [1]}] * 2, "EMBEDDING_PROFILE_MISMATCH"),
    ([{"index": 0, "embedding": [1] * 512}] * 2, "EMBEDDING_UNAVAILABLE"),
    ([{"index": i, "embedding": [0] * 512} for i in range(2)], "EMBEDDING_UNAVAILABLE"),
    ([{"index": i, "embedding": ["1"] * 512} for i in range(2)], "EMBEDDING_UNAVAILABLE"),
])
def test_rejects_invalid_remote_vectors(remote_encoder, monkeypatch, items, code):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": items}))) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        with pytest.raises(AppError) as error:
            remote_encoder.encode(["第一段", "第二段"])
    assert error.value.code == code


def test_remote_timeout_is_retryable(remote_encoder, monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        with pytest.raises(AppError) as error:
            remote_encoder.encode(["凤凰项目"])
    assert (error.value.code, error.value.status, error.value.retryable) == ("EMBEDDING_UNAVAILABLE", 503, True)
