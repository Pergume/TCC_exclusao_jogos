"""Backends de modelos: embedding, reranqueador (cross-encoder) e LLM.

Cada componente tem um backend real (Hugging Face, para o Colab) e um backend
``fake`` determinístico, usado apenas nos testes automáticos sem GPU. Os
backends ``fake`` NUNCA devem ser usados para gerar resultados da monografia.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

import numpy as np

from ..base.texto import tokenizar

_CACHE: dict[str, object] = {}


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
class EmbedderFake:
    """Saco de palavras com hashing (dimensão 256), normalizado."""
    nome = "fake-hash-256"

    def codificar(self, textos: list[str], consulta: bool = False) -> np.ndarray:
        m = np.zeros((len(textos), 256), dtype=np.float32)
        for i, t in enumerate(textos):
            for tok in tokenizar(t):
                m[i, int(hashlib.md5(tok.encode()).hexdigest(), 16) % 256] += 1.0
        n = np.linalg.norm(m, axis=1, keepdims=True)
        return m / np.maximum(n, 1e-9)


class EmbedderST:
    def __init__(self, cfg):
        from sentence_transformers import SentenceTransformer
        mcfg = cfg["modelos"]
        self.nome = mcfg["embedding"]
        self.modelo = SentenceTransformer(self.nome, device=_dispositivo())
        self.modelo.max_seq_length = mcfg["embedding_max_tokens"]
        self.lote = mcfg["embedding_lote"]
        self.pref_q = mcfg.get("embedding_prefixo_consulta", "")
        self.pref_d = mcfg.get("embedding_prefixo_documento", "")

    def codificar(self, textos: list[str], consulta: bool = False) -> np.ndarray:
        pref = self.pref_q if consulta else self.pref_d
        return self.modelo.encode([pref + t for t in textos], batch_size=self.lote, normalize_embeddings=True,
                                  convert_to_numpy=True, show_progress_bar=len(textos) > 1000).astype(np.float32)


def obter_embedder(cfg):
    chave = "emb:" + cfg["modelos"]["embedding_backend"] + cfg["modelos"]["embedding"]
    if chave not in _CACHE:
        _CACHE[chave] = EmbedderFake() if cfg["modelos"]["embedding_backend"] == "fake" else EmbedderST(cfg)
    return _CACHE[chave]


# ---------------------------------------------------------------------------
# Reranqueador
# ---------------------------------------------------------------------------
class RerankerFake:
    nome = "fake-sobreposicao"

    def pontuar(self, pares: list[tuple[str, str]]) -> list[float]:
        out = []
        for q, d in pares:
            tq, td = set(tokenizar(q)), set(tokenizar(d))
            out.append(len(tq & td) / (len(tq) or 1))
        return out


class RerankerST:
    def __init__(self, cfg):
        from sentence_transformers import CrossEncoder
        self.nome = cfg["modelos"]["reranker"]
        self.modelo = CrossEncoder(self.nome, max_length=512, device=_dispositivo())
        self.lote = cfg["modelos"]["reranker_lote"]

    def pontuar(self, pares: list[tuple[str, str]]) -> list[float]:
        return [float(x) for x in self.modelo.predict(pares, batch_size=self.lote, show_progress_bar=False)]


def obter_reranker(cfg):
    chave = "rr:" + cfg["modelos"]["reranker_backend"] + cfg["modelos"]["reranker"]
    if chave not in _CACHE:
        _CACHE[chave] = RerankerFake() if cfg["modelos"]["reranker_backend"] == "fake" else RerankerST(cfg)
    return _CACHE[chave]


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
class LLMFake:
    """Parser por regras — só para testes. Imita o formato de saída do LLM."""
    nome = "fake-regras"
    _GATILHOS = re.compile(r",?\s*(mas\s+)?(que\s+)?(não|nao)\s+(seja[m]?|tenha[m]?|estejam?|se passem?)\s+|"
                           r",?\s*(mas\s+)?sem\s+(ser\s+)?", re.I)

    def gerar(self, mensagens: list[dict]) -> str:
        pedido = mensagens[-1]["content"].split("PEDIDO:")[-1].strip()
        m = self._GATILHOS.search(pedido)
        if not m:
            return json.dumps({"consulta_positiva": pedido, "excluir": None})
        return json.dumps({"consulta_positiva": pedido[: m.start()].strip(" ,"),
                           "excluir": pedido[m.end():].strip(" .")})


class LLMTransformers:
    def __init__(self, cfg):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        m = cfg["modelos"]
        self.nome = m["llm"]
        self.tok = AutoTokenizer.from_pretrained(self.nome)
        tipo = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        mapa = "auto" if torch.cuda.is_available() else None
        try:  # transformers >= 4.56 usa `dtype`; versões anteriores, `torch_dtype`
            self.modelo = AutoModelForCausalLM.from_pretrained(self.nome, dtype=tipo, device_map=mapa)
        except TypeError:
            self.modelo = AutoModelForCausalLM.from_pretrained(self.nome, torch_dtype=tipo, device_map=mapa)
        self.max_novos = m["llm_max_novos_tokens"]

    def gerar(self, mensagens: list[dict]) -> str:
        import torch
        prompt = self.tok.apply_chat_template(mensagens, tokenize=False, add_generation_prompt=True)
        entrada = self.tok(prompt, return_tensors="pt").to(self.modelo.device)
        with torch.no_grad():
            saida = self.modelo.generate(**entrada, max_new_tokens=self.max_novos, do_sample=False,
                                         pad_token_id=self.tok.eos_token_id)
        return self.tok.decode(saida[0][entrada["input_ids"].shape[1]:], skip_special_tokens=True)


def obter_llm(cfg):
    chave = "llm:" + cfg["modelos"]["llm_backend"] + cfg["modelos"]["llm"]
    if chave not in _CACHE:
        _CACHE[chave] = LLMFake() if cfg["modelos"]["llm_backend"] == "fake" else LLMTransformers(cfg)
    return _CACHE[chave]


def extrair_json(texto: str) -> dict | None:
    """Extrai o primeiro objeto JSON da saída do LLM (tolera texto em volta)."""
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.S)
    inicio = texto.find("{")
    while inicio != -1:
        profundidade = 0
        for i in range(inicio, len(texto)):
            if texto[i] == "{":
                profundidade += 1
            elif texto[i] == "}":
                profundidade -= 1
                if profundidade == 0:
                    try:
                        return json.loads(texto[inicio:i + 1])
                    except json.JSONDecodeError:
                        break
        inicio = texto.find("{", inicio + 1)
    return None


def chamar_llm_com_cache(con, cfg, tarefa: str, mensagens: list[dict], validar) -> dict:
    """Chama o LLM uma vez por entrada (cache no SQLite) e valida a saída.

    ``validar`` recebe o dict extraído e devolve (dict_normalizado, valido).
    Saídas inválidas são guardadas com ``valido=0`` e devolvidas normalizadas
    para o comportamento de fallback (sem exclusão).
    """
    llm = obter_llm(cfg)
    entrada = json.dumps(mensagens, ensure_ascii=False)
    chave = hashlib.sha1(f"{llm.nome}|{tarefa}|{entrada}".encode()).hexdigest()
    row = con.execute("SELECT saida_json FROM llm_cache WHERE chave=?", (chave,)).fetchone()
    if row:
        return json.loads(row[0])
    bruto = llm.gerar(mensagens)
    normalizado, valido = validar(extrair_json(bruto))
    con.execute("""INSERT OR REPLACE INTO llm_cache(chave, modelo, tarefa, entrada, saida_bruta, saida_json, valido,
                   criado_em) VALUES (?,?,?,?,?,?,?,?)""",
                (chave, llm.nome, tarefa, entrada, bruto, json.dumps(normalizado, ensure_ascii=False),
                 int(valido), time.time()))
    con.commit()
    return normalizado


def _dispositivo() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"
