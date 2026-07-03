from __future__ import annotations

import math
import re
from collections import Counter

TOKEN_RE = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in TOKEN_RE.findall(text)]


def hash_embedding(text: str, dim: int = 256) -> list[float]:
    tokens = tokenize(text)
    if not tokens:
        return [0.0] * dim
    vec = [0.0] * dim
    for token in tokens:
        idx = hash(token) % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(size))


def top_keywords(text: str, top_n: int = 12) -> list[str]:
    tokens = tokenize(text)
    if not tokens:
        return []
    stop = {
        "the",
        "a",
        "an",
        "of",
        "is",
        "to",
        "and",
        "in",
        "for",
        "on",
        "with",
        "by",
        "this",
        "that",
        "it",
        "是",
        "的",
        "了",
        "在",
        "和",
        "与",
        "对",
        "中",
    }
    filtered = [t for t in tokens if t not in stop and len(t) > 1]
    return [item for item, _ in Counter(filtered).most_common(top_n)]


def split_sentences(text: str) -> list[str]:
    raw = re.split(r"[。！？!?\n]+", text)
    return [s.strip() for s in raw if s.strip()]
