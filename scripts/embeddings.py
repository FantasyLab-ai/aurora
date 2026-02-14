# /scripts/embeddings.py
from __future__ import annotations
import hashlib, math, numpy as np

_DIM = 128

def _hash_floats(s: str, dim=_DIM):
    h = hashlib.blake2b(s.encode('utf-8'), digest_size=64).digest()
    # expand to dim floats deterministically
    arr = []
    while len(arr) < dim:
        h = hashlib.blake2b(h, digest_size=64).digest()
        for i in range(0, len(h), 4):
            val = int.from_bytes(h[i:i+4], 'little', signed=False)
            arr.append((val % 20001 - 10000) / 5000.0)
            if len(arr) >= dim:
                break
    return np.array(arr, dtype=np.float32)

def embed_reduced(text: str, dim=_DIM) -> np.ndarray:
    text = (text or "").strip().lower()
    if not text:
        v = np.zeros(dim, dtype=np.float32); v[0]=1.0; return v
    toks = [t for t in text.replace("\n"," ").split(" ") if t]
    acc = np.zeros(dim, dtype=np.float32)
    for t in toks[:64]:  # cap
        acc += _hash_floats(t, dim)
    # l2 normalize
    n = float(np.linalg.norm(acc))
    if n == 0: acc[0]=1.0; n=1.0
    return acc / n
