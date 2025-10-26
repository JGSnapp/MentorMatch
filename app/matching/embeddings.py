from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import torch
from psycopg2 import sql
from psycopg2.extensions import connection
from sentence_transformers import SentenceTransformer
from transformers import AutoModel, AutoTokenizer

DEFAULT_MODEL_REPO_ID = "intfloat/multilingual-e5-base"

ALLOWED_REPO_IDS = {
    "intfloat/multilingual-e5-small",
    "cointegrated/rubert-tiny2",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "ai-forever/sbert_large_nlu_ru",
    DEFAULT_MODEL_REPO_ID,
    "BAAI/bge-m3",
}

_models_dir_override = os.getenv("EMBEDDING_MODELS_DIR")
if _models_dir_override:
    MODELS_DIR = Path(_models_dir_override).expanduser().resolve()
else:
    MODELS_DIR = Path(__file__).resolve().parent / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
_MODEL_CACHE: Dict[str, "EmbeddingModel"] = {}


def _should_use_sentence_transformers(repo_id: str) -> bool:
    name = repo_id.lower()
    return "cointegrated/rubert-tiny2" not in name


def _model_cache_dir(repo_id: str) -> Path:
    safe_name = repo_id.replace("/", "__")
    cache_dir = MODELS_DIR / safe_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@dataclass
class EmbeddingModel:
    repo_id: str
    backend: str
    local_dir: Path
    model: Union[SentenceTransformer, AutoModel]
    tokenizer: Optional[AutoTokenizer] = None

    def __post_init__(self) -> None:
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if self.backend == "sentence-transformers":
            # SentenceTransformer expects device as a string identifier.
            self.model.to(str(self.device))
        else:
            self.model.to(self.device)
        self.output_dimension = self._infer_output_dimension()

    def _infer_output_dimension(self) -> int:
        if self.backend == "sentence-transformers":
            return self.model.get_sentence_embedding_dimension()
        return int(getattr(self.model.config, "hidden_size", 0))

    @classmethod
    def from_pretrained(
        cls,
        repo_id: str,
        *,
        revision: Optional[str] = None,
        token: Optional[str] = None,
    ) -> "EmbeddingModel":
        backend = (
            "sentence-transformers"
            if _should_use_sentence_transformers(repo_id)
            else "transformers"
        )
        local_dir = _model_cache_dir(repo_id)
        if backend == "sentence-transformers":
            model = SentenceTransformer(
                repo_id,
                revision=revision,
                cache_folder=str(local_dir),
                use_auth_token=token,
            )
            tokenizer = None
        else:
            tokenizer = AutoTokenizer.from_pretrained(
                repo_id,
                revision=revision,
                token=token,
                cache_dir=str(local_dir),
            )
            model = AutoModel.from_pretrained(
                repo_id,
                revision=revision,
                token=token,
                cache_dir=str(local_dir),
            )
        return cls(
            repo_id=repo_id,
            backend=backend,
            local_dir=local_dir,
            model=model,
            tokenizer=tokenizer,
        )

    def encode(
        self,
        texts: Union[str, Sequence[str]],
        *,
        normalize: bool = True,
        batch_size: Optional[int] = None,
    ) -> np.ndarray:
        batched_texts, single_input = _ensure_batched(texts)
        if self.backend == "sentence-transformers":
            encode_kwargs = {
                "device": str(self.device),
                "normalize_embeddings": normalize,
                "convert_to_numpy": True,
            }
            if batch_size is not None:
                encode_kwargs["batch_size"] = batch_size
            embeddings = self.model.encode(batched_texts, **encode_kwargs)
        else:
            embeddings = self._encode_with_transformers(
                batched_texts,
                normalize=normalize,
            )
        if single_input:
            return embeddings[0]
        return embeddings

    def _encode_with_transformers(
        self,
        texts: Sequence[str],
        *,
        normalize: bool,
    ) -> np.ndarray:
        if self.tokenizer is None:
            raise RuntimeError("Tokenizer is not initialised for transformers backend.")
        inputs = self.tokenizer(
            list(texts),
            padding=True,
            truncation=True,
            return_tensors="pt",
        ).to(self.device)
        with torch.no_grad():
            model_output = self.model(**inputs)
        pooled_embeddings = _mean_pooling(
            model_output.last_hidden_state,
            inputs["attention_mask"],
        )
        if normalize:
            pooled_embeddings = torch.nn.functional.normalize(
                pooled_embeddings,
                p=2,
                dim=1,
            )
        return pooled_embeddings.cpu().numpy()


def _ensure_batched(texts: Union[str, Sequence[str]]) -> Tuple[Sequence[str], bool]:
    if isinstance(texts, str):
        return [texts], True
    return list(texts), False


def _vector_to_pgvector(vector: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in vector.tolist()) + "]"


def _mean_pooling(
    last_hidden_state: torch.Tensor,
    attention_mask: torch.Tensor,
) -> torch.Tensor:
    mask = attention_mask.unsqueeze(-1).expand(last_hidden_state.size()).float()
    masked_embeddings = last_hidden_state * mask
    sum_embeddings = torch.sum(masked_embeddings, dim=1)
    sum_mask = torch.clamp(mask.sum(dim=1), min=1e-9)
    return sum_embeddings / sum_mask


def load_embedding_model(
    repo_id: str,
    *,
    revision: Optional[str] = None,
    token: Optional[str] = None,
) -> EmbeddingModel:
    if repo_id not in ALLOWED_REPO_IDS:
        raise ValueError(f"Model {repo_id} is not whitelisted for download.")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return EmbeddingModel.from_pretrained(
        repo_id,
        revision=revision,
        token=token,
    )


def _get_cached_model(repo_id: str) -> EmbeddingModel:
    model = _MODEL_CACHE.get(repo_id)
    if model is None:
        model = load_embedding_model(repo_id)
        _MODEL_CACHE[repo_id] = model
    return model


def _extract_value(entity: Union[Mapping[str, Any], Any], key: str) -> Any:
    if isinstance(entity, Mapping):
        return entity.get(key)
    return getattr(entity, key, None)


def _stringify_parts(parts: Sequence[Any]) -> str:
    serialized: List[str] = []
    for value in parts:
        if value in (None, "", [], ()):
            continue
        if isinstance(value, (list, tuple, set)):
            flattened = " ".join(str(item).strip() for item in value if item not in (None, ""))
            if flattened:
                serialized.append(flattened)
            continue
        text = str(value).strip()
        if text:
            serialized.append(text)
    return "\n".join(serialized)


def _build_entity_text(entity: Union[Mapping[str, Any], Any], entity_type: str) -> str:
    entity_type = (entity_type or "").strip().lower()
    if entity_type not in {"student", "supervisor", "topic", "role"}:
        raise ValueError(f"Unsupported entity type: {entity_type}")

    pieces: List[Any] = []
    if entity_type in {"student", "supervisor"}:
        pieces.extend(
            [
                _extract_value(entity, "full_name"),
                _extract_value(entity, "email"),
                _extract_value(entity, "username"),
            ]
        )

    if entity_type == "student":
        pieces.extend(
            [
                _extract_value(entity, "program"),
                _extract_value(entity, "skills"),
                _extract_value(entity, "skills_to_learn"),
                _extract_value(entity, "interests"),
                _extract_value(entity, "achievements"),
                _extract_value(entity, "preferred_team_track"),
                _extract_value(entity, "team_role"),
                _extract_value(entity, "team_needs"),
                _extract_value(entity, "team_has"),
                _extract_value(entity, "dev_track"),
                _extract_value(entity, "science_track"),
                _extract_value(entity, "startup_track"),
                _extract_value(entity, "cv"),
                _extract_value(entity, "requirements"),
                _extract_value(entity, "final_work_pref"),
            ]
        )
    elif entity_type == "supervisor":
        pieces.extend(
            [
                _extract_value(entity, "position"),
                _extract_value(entity, "degree"),
                _extract_value(entity, "capacity"),
                _extract_value(entity, "interests"),
                _extract_value(entity, "requirements"),
            ]
        )
    elif entity_type == "topic":
        pieces.extend(
            [
                _extract_value(entity, "title"),
                _extract_value(entity, "description"),
                _extract_value(entity, "expected_outcomes"),
                _extract_value(entity, "required_skills"),
                _extract_value(entity, "direction"),
                _extract_value(entity, "author_name"),
                _extract_value(entity, "seeking_role"),
            ]
        )
    elif entity_type == "role":
        pieces.extend(
            [
                _extract_value(entity, "name"),
                _extract_value(entity, "description"),
                _extract_value(entity, "required_skills"),
                _extract_value(entity, "capacity"),
                _extract_value(entity, "direction"),
                _extract_value(entity, "seeking_role"),
            ]
        )
        topic_payload = _extract_value(entity, "topic")
        topic_parts: List[Any] = []
        if isinstance(topic_payload, Mapping):
            topic_parts.extend(
                [
                    topic_payload.get("title"),
                    topic_payload.get("description"),
                    topic_payload.get("expected_outcomes"),
                    topic_payload.get("required_skills"),
                    topic_payload.get("direction"),
                    topic_payload.get("author_name"),
                    topic_payload.get("seeking_role"),
                ]
            )
        pieces.extend(
            [
                _extract_value(entity, "topic_title"),
                _extract_value(entity, "topic_description"),
                _extract_value(entity, "topic_expected_outcomes"),
                _extract_value(entity, "topic_required_skills"),
                _extract_value(entity, "author_name"),
            ]
        )
        if topic_parts:
            pieces.extend(topic_parts)

    text = _stringify_parts(pieces)
    if not text:
        raise ValueError("No textual content available to build embedding payload.")
    return text


def _resolve_storage(
    entity: Union[Mapping[str, Any], Any], entity_type: str
) -> Tuple[str, str, Any]:
    entity_type = (entity_type or "").strip().lower()
    if entity_type in {"student", "supervisor"}:
        table = "users"
        id_candidates = ("user_id", "id")
        column = "id"
    elif entity_type == "topic":
        table = "topics"
        id_candidates = ("topic_id", "id")
        column = "id"
    elif entity_type == "role":
        table = "roles"
        id_candidates = ("role_id", "id")
        column = "id"
    else:
        raise ValueError(f"Unsupported entity type for storage: {entity_type}")

    entity_id = None
    for key in id_candidates:
        value = _extract_value(entity, key)
        if value not in (None, ""):
            entity_id = value
            break
    if entity_id is None:
        raise ValueError("Unable to resolve entity identifier for embedding storage.")
    return table, column, entity_id


def generate_and_store_embedding(
    conn: connection,
    entity: Union[Mapping[str, Any], Any],
    entity_type: str,
    *,
    model: Optional[EmbeddingModel] = None,
    model_repo_id: str = DEFAULT_MODEL_REPO_ID,
    normalize: bool = True,
    commit: bool = True,
) -> np.ndarray:
    """
    Build textual representation of the entity, compute its embedding and persist to DB.
    """

    text = _build_entity_text(entity, entity_type)
    embedding_model = model or _get_cached_model(model_repo_id)
    vector = embedding_model.encode(text, normalize=normalize)
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector = vector / norm
    payload = _vector_to_pgvector(vector)

    table, id_column, entity_id = _resolve_storage(entity, entity_type)
    query = sql.SQL(
        "UPDATE {table} SET embeddings=%s::vector, updated_at=now() WHERE {id_column}=%s"
    ).format(table=sql.Identifier(table), id_column=sql.Identifier(id_column))

    with conn.cursor() as cur:
        cur.execute(query, (payload, entity_id))
    if commit:
        conn.commit()
    return vector


def refresh_student_embedding(
    conn: connection,
    student_user_id: int,
    *,
    model_repo_id: str = DEFAULT_MODEL_REPO_ID,
    normalize: bool = True,
    commit: bool = False,
) -> Optional[np.ndarray]:
    from .repository import fetch_student  # Local import to avoid circular dependency.

    student = fetch_student(conn, student_user_id)
    if not student:
        return None
    return generate_and_store_embedding(
        conn,
        student,
        "student",
        model_repo_id=model_repo_id,
        normalize=normalize,
        commit=commit,
    )


def refresh_supervisor_embedding(
    conn: connection,
    supervisor_user_id: int,
    *,
    model_repo_id: str = DEFAULT_MODEL_REPO_ID,
    normalize: bool = True,
    commit: bool = False,
) -> Optional[np.ndarray]:
    from .repository import fetch_supervisor

    supervisor = fetch_supervisor(conn, supervisor_user_id)
    if not supervisor:
        return None
    return generate_and_store_embedding(
        conn,
        supervisor,
        "supervisor",
        model_repo_id=model_repo_id,
        normalize=normalize,
        commit=commit,
    )


def refresh_topic_embedding(
    conn: connection,
    topic_id: int,
    *,
    model_repo_id: str = DEFAULT_MODEL_REPO_ID,
    normalize: bool = True,
    commit: bool = False,
) -> Optional[np.ndarray]:
    from .repository import fetch_topic

    topic = fetch_topic(conn, topic_id)
    if not topic:
        return None
    return generate_and_store_embedding(
        conn,
        topic,
        "topic",
        model_repo_id=model_repo_id,
        normalize=normalize,
        commit=commit,
    )


def refresh_role_embedding(
    conn: connection,
    role_id: int,
    *,
    model_repo_id: str = DEFAULT_MODEL_REPO_ID,
    normalize: bool = True,
    commit: bool = False,
) -> Optional[np.ndarray]:
    from .repository import fetch_role

    role = fetch_role(conn, role_id)
    if not role:
        return None
    return generate_and_store_embedding(
        conn,
        role,
        "role",
        model_repo_id=model_repo_id,
        normalize=normalize,
        commit=commit,
    )


def pull_model(
    repo_id: str,
    *,
    revision: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, str]:
    model = load_embedding_model(
        repo_id,
        revision=revision,
        token=token,
    )
    detail = f"dim={model.output_dimension}; local_dir={model.local_dir}"
    return {
        "repo_id": repo_id,
        "backend": model.backend,
        "status": "OK",
        "detail": detail,
    }


__all__ = [
    "EmbeddingModel",
    "load_embedding_model",
    "generate_and_store_embedding",
    "refresh_student_embedding",
    "refresh_supervisor_embedding",
    "refresh_topic_embedding",
    "refresh_role_embedding",
    "pull_model",
]
