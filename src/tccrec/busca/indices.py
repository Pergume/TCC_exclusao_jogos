"""Índices de busca: BM25 (rank_bm25, em memória + pickle) e denso (ChromaDB).

Os dois índices são construídos sobre a tabela ``documentos``, separados por
fonte (``descricao_tags`` e ``reviews``). Na fonte ``reviews`` há vários
documentos por jogo; as buscas devolvem documentos e a agregação por jogo
(MaxP) é feita em :mod:`tccrec.busca.recuperadores`.
"""
from __future__ import annotations

import pickle
import re
import time
from pathlib import Path

import numpy as np

from ..base.texto import tokenizar
from ..config import dir_dados
from ..db import conectar, registrar_evento
from .modelos import obter_embedder

FONTES = ("descricao_tags", "reviews")


def dir_indices() -> Path:
    d = dir_dados() / "indices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:40]


def nome_colecao(cfg, fonte: str) -> str:
    return f"{fonte}__{_slug(cfg['modelos']['embedding'] if cfg['modelos']['embedding_backend'] != 'fake' else 'fake')}"


def carregar_documentos(con, fonte: str) -> list[tuple[str, int, str]]:
    return [(r[0], r[1], r[2]) for r in
            con.execute("SELECT doc_id, appid, texto FROM documentos WHERE fonte=? ORDER BY doc_id", (fonte,))]


# ---------------------------------------------------------------- BM25
class IndiceBM25:
    def __init__(self, doc_ids: list[str], appids: list[int], tokens: list[list[str]]):
        from rank_bm25 import BM25Okapi
        self.doc_ids, self.appids = doc_ids, appids
        self.bm25 = BM25Okapi(tokens)

    def buscar(self, consulta: str, n: int) -> list[tuple[str, int, float]]:
        notas = self.bm25.get_scores(tokenizar(consulta))
        topo = np.argsort(-notas)[:n]
        return [(self.doc_ids[i], self.appids[i], float(notas[i])) for i in topo if notas[i] > 0]


def construir_bm25(con, fonte: str) -> IndiceBM25:
    docs = carregar_documentos(con, fonte)
    idx = IndiceBM25([d[0] for d in docs], [d[1] for d in docs], [tokenizar(d[2]) for d in docs])
    with open(dir_indices() / f"bm25_{fonte}.pkl", "wb") as f:
        pickle.dump(idx, f)
    return idx


def carregar_bm25(con, fonte: str) -> IndiceBM25:
    caminho = dir_indices() / f"bm25_{fonte}.pkl"
    if caminho.exists():
        with open(caminho, "rb") as f:
            return pickle.load(f)
    return construir_bm25(con, fonte)


# ---------------------------------------------------------------- Denso
def cliente_chroma():
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(path=str(dir_indices() / "chroma"),
                                     settings=Settings(anonymized_telemetry=False))


class IndiceDenso:
    def __init__(self, cfg, fonte: str):
        self.cfg = cfg
        self.colecao = cliente_chroma().get_collection(nome_colecao(cfg, fonte))
        self.embedder = obter_embedder(cfg)

    def vetor(self, texto: str) -> np.ndarray:
        return self.embedder.codificar([texto], consulta=True)[0]

    def buscar_vetor(self, v: np.ndarray, n: int) -> list[tuple[str, int, float]]:
        r = self.colecao.query(query_embeddings=[v.tolist()], n_results=n, include=["metadatas", "distances"])
        return [(i, int(m["appid"]), 1.0 - float(d))  # distância cosseno → similaridade
                for i, m, d in zip(r["ids"][0], r["metadatas"][0], r["distances"][0])]

    def buscar(self, consulta: str, n: int) -> list[tuple[str, int, float]]:
        return self.buscar_vetor(self.vetor(consulta), n)

    def vetores_dos_jogos(self, appids: list[int]) -> tuple[list[str], list[int], np.ndarray]:
        """Todos os documentos (e embeddings) dos jogos informados — usado em C5."""
        if not appids:
            return [], [], np.zeros((0, 1), dtype=np.float32)
        r = self.colecao.get(where={"appid": {"$in": [int(a) for a in appids]}}, include=["embeddings", "metadatas"])
        return r["ids"], [int(m["appid"]) for m in r["metadatas"]], np.asarray(r["embeddings"], dtype=np.float32)


def construir_denso(cfg, con, fonte: str, lote: int = 2048) -> int:
    cli = cliente_chroma()
    nome = nome_colecao(cfg, fonte)
    try:
        cli.delete_collection(nome)
    except Exception:
        pass
    col = cli.create_collection(nome, metadata={"hnsw:space": "cosine", "modelo": cfg["modelos"]["embedding"]})
    emb = obter_embedder(cfg)
    docs = carregar_documentos(con, fonte)
    t0 = time.time()
    for i in range(0, len(docs), lote):
        parte = docs[i:i + lote]
        vetores = emb.codificar([d[2] for d in parte])
        col.add(ids=[d[0] for d in parte], embeddings=vetores.tolist(),
                metadatas=[{"appid": int(d[1])} for d in parte])
        print(f"denso {fonte}: {min(i + lote, len(docs))}/{len(docs)}", flush=True)
    registrar_evento(con, "indexar", f"índice denso {nome}: {len(docs)} documentos em {time.time() - t0:.0f}s")
    return len(docs)


def indexar(cfg, fonte: str = "todas", so_bm25: bool = False) -> None:
    con = conectar()
    for f in (FONTES if fonte == "todas" else (fonte,)):
        idx = construir_bm25(con, f)
        registrar_evento(con, "indexar", f"BM25 {f}: {len(idx.doc_ids)} documentos")
        if not so_bm25:
            construir_denso(cfg, con, f)
