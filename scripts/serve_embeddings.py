"""Run on the GPU host, using QZhou's complete SentenceTransformer model."""
import argparse
import hmac
import os
import threading

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

MODEL_NAMES = ("Kingsoft-LLM/QZhou-Embedding-Zh", "qzhou-embedding-zh")


class EmbeddingRequest(BaseModel):
    model: str
    input: list[str] = Field(min_length=1, max_length=32)


def create_app(model, api_key):
    if not api_key:
        raise ValueError("Set EMBEDDING_API_KEY on the GPU host")
    app = FastAPI(title="QZhou Embedding Demo")
    lock = threading.Lock()

    def authorize(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization.encode(), f"Bearer {api_key}".encode()):
            raise HTTPException(401, "Unauthorized")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    def models():
        return {"object": "list", "data": [{"id": name, "object": "model"} for name in MODEL_NAMES]}

    @app.post("/v1/embeddings", dependencies=[Depends(authorize)])
    def embeddings(body: EmbeddingRequest):
        if body.model not in MODEL_NAMES:
            raise HTTPException(404, "Unknown embedding model")
        if any(not text.strip() for text in body.input):
            raise HTTPException(422, "Input text must not be blank")
        # One GPU worker; serialize tokenization and inference to bound memory.
        with lock:
            tokens = model.tokenizer(body.input, truncation=False, padding=False)["input_ids"]
            if any(len(ids) > model.max_seq_length for ids in tokens):
                raise HTTPException(422, f"Input exceeds {model.max_seq_length} tokens; split it first")
            # Inputs already contain the client's PromptEOL template. The complete
            # model performs Token Prepending, last-token pooling, and learned MRL.
            vectors = np.asarray(model.encode(
                body.input, batch_size=2, normalize_embeddings=True,
                convert_to_numpy=True, show_progress_bar=False,
            ))
        if (vectors.shape != (len(body.input), 1792)
                or not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors, axis=1) == 0)):
            raise HTTPException(500, "Invalid QZhou projection output")
        return {"object": "list", "model": body.model, "data": [
            {"object": "embedding", "index": i, "embedding": vector.tolist()}
            for i, vector in enumerate(vectors)
        ]}

    return app


def main():
    import torch
    import uvicorn
    from sentence_transformers import SentenceTransformer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--port", type=int, default=6006)
    args = parser.parse_args()
    api_key = os.environ.get("EMBEDDING_API_KEY", "")
    if not api_key:
        parser.error("Set EMBEDDING_API_KEY")
    model = SentenceTransformer(
        args.model_dir, device="cuda", trust_remote_code=True,
        model_kwargs={"torch_dtype": torch.bfloat16, "attn_implementation": "sdpa"},
        tokenizer_kwargs={"padding_side": "left"},
    )
    model.max_seq_length = 4096
    if model.get_sentence_embedding_dimension() != 1792:
        raise RuntimeError("The complete QZhou model, including 2_Dense, is required")
    uvicorn.run(create_app(model, api_key), host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
