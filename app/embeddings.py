import gzip
from pathlib import Path

import httpx
import numpy as np
from tokenizers import Tokenizer

from app.errors import AppError

QUERY_INSTRUCTION = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
INPUT_TEMPLATE = 'This sentence: <|im_start|>“{text}” means in one word: “'
DIMENSION = 512
FULL_DIMENSION = 1792
MAX_INPUT_TOKENS = 40960


class Embeddings:
    def __init__(self, settings):
        self.settings = settings
        self.endpoint = None

    def load(self):
        url = self.settings.embedding_base_url
        if url is not None:
            if any(part is not None for part in (url.username, url.password, url.query, url.fragment)):
                raise AppError("EMBEDDING_UNAVAILABLE", "EMBEDDING_BASE_URL 只接受服务地址和 API 前缀", 503)
            self.endpoint = str(url).rstrip("/") + "/embeddings"
        with gzip.open(Path(__file__).with_name("qzhou_tokenizer.json.gz"), "rb") as file:
            self.tokenizer = Tokenizer.from_buffer(file.read())
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()

    def token_count(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=True).ids)

    def encode(self, texts, kind="document"):
        if not texts:
            return np.empty((0, DIMENSION), dtype=np.float32)
        if self.endpoint is None:
            raise AppError("EMBEDDING_UNAVAILABLE", "请在 .env 中设置 EMBEDDING_BASE_URL", 503)
        prefix = QUERY_INSTRUCTION if kind == "query" else ""
        inputs = [INPUT_TEMPLATE.format(text=prefix + text) for text in texts]
        if any(self.token_count(text) > MAX_INPUT_TOKENS for text in inputs):
            raise AppError("INPUT_TOO_LONG", "编码输入超过 QZhou 的长度上限，请缩短问题或拆分文本", 422)
        api_key = self.settings.embedding_api_key.get_secret_value()
        try:
            response = httpx.post(
                self.endpoint,
                json={"model": self.settings.embedding_model, "input": inputs},
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                timeout=self.settings.embedding_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise AppError("EMBEDDING_UNAVAILABLE",
                               f"向量服务返回 HTTP {status}，请检查 EMBEDDING_API_KEY 是否与服务端一致",
                               503) from exc
            raise AppError("EMBEDDING_UNAVAILABLE", f"向量服务返回 HTTP {status}，请检查地址与模型配置",
                           503, status == 429 or status >= 500) from exc
        except httpx.RequestError as exc:
            raise AppError("EMBEDDING_UNAVAILABLE", "连接向量服务失败或请求超时，请检查服务和网络",
                           503, True) from exc

        try:
            items = response.json()["data"]
            if not isinstance(items, list) or len(items) != len(inputs):
                raise ValueError("Wrong embedding count")
            vectors = [None] * len(inputs)
            for item in items:
                index, vector = item["index"], item["embedding"]
                if type(index) is not int or not 0 <= index < len(inputs) or vectors[index] is not None:
                    raise ValueError("Invalid or duplicate embedding index")
                if not isinstance(vector, list) or any(type(value) not in (int, float) for value in vector):
                    raise ValueError("Embedding must be a list of numbers")
                if len(vector) not in (DIMENSION, FULL_DIMENSION):
                    raise AppError("EMBEDDING_PROFILE_MISMATCH", "QZhou 服务须返回 512 或 1792 维向量", 409)
                vectors[index] = vector
            matrix = np.asarray(vectors, dtype=np.float64)
            if not np.isfinite(matrix).all():
                raise ValueError("Non-finite embedding")
            # QZhou MRL uses the leading dimensions of the learned projection before normalization.
            matrix = matrix[:, :DIMENSION]
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            if not np.isfinite(norms).all() or np.any(norms == 0):
                raise ValueError("Invalid embedding norm")
        except (IndexError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise AppError("EMBEDDING_UNAVAILABLE", "向量服务返回了无效的编码结果", 503) from exc
        return (matrix / norms).astype(np.float32)
