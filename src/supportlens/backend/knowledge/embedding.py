"""Fixed, digest-pinned local embeddings shared by future retrieval consumers."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Protocol, Sequence

MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_KEY = f"minilm-l6-v2:{MODEL_REVISION}:avx2-single-mean-l2-v1"
DIMENSION = 384
DIRECTORY = Path(__file__).resolve().parents[4] / "artifacts/models/m4b_minilm"
FILES = {
    "tokenizer.json": (466247, "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037"),
    "model_quint8_avx2.onnx": (23046789, "b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21"),
}


class EmbeddingUnavailable(RuntimeError):
    """No trusted embedding is available for this request."""


@dataclass(frozen=True)
class EmbeddingIdentity:
    key: str
    dimension: int


class Embedder(Protocol):
    identity: EmbeddingIdentity

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


def verified_bytes(name: str) -> bytes:
    size, digest = FILES[name]
    target = DIRECTORY / name
    if target.resolve() != target or target.is_symlink():
        raise EmbeddingUnavailable("Embedding artifact path is invalid.")
    with target.open("rb") as file:
        artifact = file.read(size + 1)
    if len(artifact) != size or hashlib.sha256(artifact).hexdigest() != digest:
        raise EmbeddingUnavailable("Embedding artifact integrity check failed.")
    return artifact


class LocalEmbedder:
    identity = EmbeddingIdentity(MODEL_KEY, DIMENSION)

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._model = None
        self._retry_at = 0.0
        self._lock = Lock()

    def _load(self):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_str(verified_bytes("tokenizer.json").decode("utf-8"))
        tokenizer.enable_truncation(max_length=256)
        tokenizer.enable_padding(length=256)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            verified_bytes("model_quint8_avx2.onnx"), options, providers=["CPUExecutionProvider"],
        )
        return tokenizer, session

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.enabled:
            raise EmbeddingUnavailable("Local embeddings are disabled.")
        if not texts or len(texts) > 16 or any(not value.strip() or len(value) > 4000 for value in texts):
            raise ValueError("Expected 1 to 16 bounded nonblank embedding inputs.")
        try:
            import numpy as np

            with self._lock:
                if self._model is None:
                    if monotonic() < self._retry_at:
                        raise EmbeddingUnavailable("Embedding model is temporarily unavailable.")
                    self._retry_at = monotonic() + 30
                    self._model = self._load()
                tokenizer, session = self._model
                rows = tokenizer.encode_batch(list(texts))
                # Dynamic quantization depends on activations. Process one row per
                # inference so reindexing is independent of batch composition.
                pooled = []
                for row in rows:
                    inputs = {
                        "input_ids": np.asarray([row.ids], dtype=np.int64),
                        "attention_mask": np.asarray([row.attention_mask], dtype=np.int64),
                        "token_type_ids": np.asarray([row.type_ids], dtype=np.int64),
                    }
                    hidden = session.run(["last_hidden_state"], inputs)[0]
                    mask = inputs["attention_mask"][..., None]
                    pooled.append((hidden * mask).sum(axis=1)[0] / mask.sum())
            vectors = np.asarray(pooled)
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if vectors.shape != (len(texts), DIMENSION) or not np.isfinite(vectors).all() or (norms <= 0).any():
                raise EmbeddingUnavailable("Embedding model produced invalid vectors.")
            return (vectors / norms).astype(np.float32).tolist()
        except Exception:
            raise EmbeddingUnavailable("Local embeddings are unavailable.") from None
