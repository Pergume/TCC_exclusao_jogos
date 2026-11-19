"""As seis configurações de busca comparadas no TCC.

| Config | Funcionamento                                                     |
|--------|-------------------------------------------------------------------|
| C1     | BM25 sobre o pedido original                                      |
| C2     | Busca densa (embedding) sobre o pedido original                   |
| C3     | Híbrida: fusão de C1 e C2 por posição (RRF, k = 60)               |
| C4     | C3 + reranqueamento com cross-encoder                             |
| C5     | LLM separa alvo/armadilha; nota = s(alvo) − β·s(armadilha)        |
| C6     | LLM extrai a exclusão como tags/títulos; C3 sobre a consulta      |
|        | positiva e filtro dos jogos com o atributo excluído               |

Variantes registradas como execuções próprias: ``C5-b<β>`` (sensibilidade a β)
e ``C6r`` (só a reescrita da consulta, sem o filtro — ablação de C6).

Na fonte ``reviews`` cada jogo tem vários documentos; a nota do jogo é a maior
nota entre seus documentos (MaxP). Todas as configurações devolvem até
``profundidade_candidatos`` jogos por consulta, gravados em ``rankings``.
"""
from __future__ import annotations

import json
import time

import numpy as np

from ..base.preparar import carregar_traducoes_tags
from ..config import hash_config
from ..db import conectar, registrar_evento
from ..teste.conjunto import normalizar_nome
from .indices import IndiceDenso, carregar_bm25
from .modelos import chamar_llm_com_cache, obter_reranker
from .prompts import mensagens_extrair, mensagens_separar, validar_extrair, validar_separar

Resultado = list[tuple[int, float, str]]  # (appid, nota, doc_id de evidência)


def agregar_por_jogo(hits: list[tuple[str, int, float]], n: int) -> Resultado:
    """MaxP: cada jogo recebe a maior nota entre seus documentos."""
    melhor: dict[int, tuple[float, str]] = {}
    for doc_id, appid, nota in hits:
        if appid not in melhor or nota > melhor[appid][0]:
            melhor[appid] = (nota, doc_id)
    ordem = sorted(melhor.items(), key=lambda kv: -kv[1][0])[:n]
    return [(a, s, d) for a, (s, d) in ordem]


def rrf(listas: list[Resultado], k: int, n: int) -> Resultado:
    nota: dict[int, float] = {}
    evid: dict[int, str] = {}
    for lista in listas:
        for pos, (appid, _, doc) in enumerate(lista, 1):
            nota[appid] = nota.get(appid, 0.0) + 1.0 / (k + pos)
            evid.setdefault(appid, doc)
    ordem = sorted(nota.items(), key=lambda kv: -kv[1])[:n]
    return [(a, s, evid[a]) for a, s in ordem]


class Buscador:
    """Reúne os índices e modelos de uma fonte e implementa C1–C6."""

    def __init__(self, cfg, con, fonte: str, precisa_denso: bool = True):
        self.cfg, self.con, self.fonte = cfg, con, fonte
        b = cfg["busca"]
        self.n = b["profundidade_candidatos"]
        # Na fonte reviews, mais documentos são buscados para cobrir N jogos.
        self.n_docs = self.n if fonte == "descricao_tags" else self.n * 20
        self.bm25 = carregar_bm25(con, fonte)
        self.denso = IndiceDenso(cfg, fonte) if precisa_denso else None
        self.textos = dict(con.execute("SELECT doc_id, texto FROM documentos WHERE fonte=?", (fonte,)).fetchall())
        top_n = cfg["base"]["tags_top_n"]
        self.tags: dict[int, set[str]] = {}
        for r in con.execute("SELECT appid, tag FROM tags WHERE posicao <= ?", (top_n,)):
            self.tags.setdefault(r[0], set()).add(r[1])
        sel = {r[0] for r in con.execute("SELECT appid FROM selecionados")}
        freq: dict[str, int] = {}
        for a, ts in self.tags.items():
            if a in sel:
                for t in ts:
                    freq[t] = freq.get(t, 0) + 1
        self.vocabulario = [t for t, _ in sorted(freq.items(), key=lambda kv: -kv[1])][:400]
        self.traducoes = carregar_traducoes_tags()
        self.nomes = {r[0]: normalizar_nome(r[1]) for r in con.execute("SELECT appid, nome FROM jogos")}
        self.devs = {r[0]: normalizar_nome(r[1] + " " + r[2]) for r in
                     con.execute("SELECT appid, desenvolvedores, publicadoras FROM jogos")}

    # ------------------------------------------------------------ C1 a C4
    def c1(self, q: str) -> Resultado:
        return agregar_por_jogo(self.bm25.buscar(q, self.n_docs), self.n)

    def c2(self, q: str) -> Resultado:
        return agregar_por_jogo(self.denso.buscar(q, self.n_docs), self.n)

    def c3(self, q: str) -> Resultado:
        return rrf([self.c1(q), self.c2(q)], self.cfg["busca"]["rrf_k"], self.n)

    def c4(self, q: str) -> Resultado:
        prof = self.cfg["busca"]["rerank_profundidade"]
        l1, l2 = self.c1(q), self.c2(q)
        base = rrf([l1, l2], self.cfg["busca"]["rrf_k"], self.n)
        evid: dict[int, set[str]] = {}
        for lista in (l1, l2):
            for a, _, d in lista:
                evid.setdefault(a, set()).add(d)
        topo = base[:prof]
        pares, donos = [], []
        for a, _, _ in topo:
            for d in sorted(evid.get(a, set())):
                pares.append((q, self.textos[d]))
                donos.append((a, d))
        notas = obter_reranker(self.cfg).pontuar(pares) if pares else []
        melhor: dict[int, tuple[float, str]] = {}
        for (a, d), s in zip(donos, notas):
            if a not in melhor or s > melhor[a][0]:
                melhor[a] = (s, d)
        reordenado = sorted(melhor.items(), key=lambda kv: -kv[1][0])
        resto = [x for x in base[prof:]]  # além da profundidade, mantém a ordem de C3
        return [(a, s, d) for a, (s, d) in reordenado] + [(a, -1e9 + s, d) for a, s, d in resto]

    # ------------------------------------------------------------ C5
    def separar(self, q: str) -> dict:
        return chamar_llm_com_cache(self.con, self.cfg, "separar", mensagens_separar(q),
                                    lambda d: validar_separar(d, q))

    def c5(self, q: str, beta: float) -> Resultado:
        s = self.separar(q)
        alvo, armadilha = s["consulta_positiva"], s["excluir"]
        if not armadilha:
            return self.c2(alvo)
        v_alvo, v_arm = self.denso.vetor(alvo), self.denso.vetor(armadilha)
        candidatos = agregar_por_jogo(self.denso.buscar_vetor(v_alvo, self.n_docs), self.n)
        ids, appids, m = self.denso.vetores_dos_jogos([a for a, _, _ in candidatos])
        if not ids:
            return candidatos
        notas = m @ v_alvo - beta * (m @ v_arm)
        return agregar_por_jogo(list(zip(ids, appids, notas.tolist())), self.n)

    # ------------------------------------------------------------ C6
    def extrair(self, q: str) -> dict:
        voc = set(self.vocabulario)
        return chamar_llm_com_cache(self.con, self.cfg, "extrair_tags",
                                    mensagens_extrair(q, self.vocabulario, self.traducoes),
                                    lambda d: validar_extrair(d, q, voc))

    def _viola_extraido(self, appid: int, e: dict) -> bool:
        if self.tags.get(appid, set()) & set(e["excluir_tags"]):
            return True
        nome = self.nomes.get(appid, "")
        if any(normalizar_nome(t) and normalizar_nome(t) in nome for t in e["excluir_titulos"]):
            return True
        devs = self.devs.get(appid, "")
        return any(normalizar_nome(d) and normalizar_nome(d) in devs for d in e["excluir_desenvolvedores"])

    def c6(self, q: str, filtrar: bool = True) -> Resultado:
        e = self.extrair(q)
        consulta = e["consulta_positiva"]
        b = self.cfg["busca"]
        n_amplo = self.n * 3  # busca mais fundo para sobrar o suficiente após o filtro
        l1 = agregar_por_jogo(self.bm25.buscar(consulta, self.n_docs * 3), n_amplo)
        l2 = agregar_por_jogo(self.denso.buscar(consulta, self.n_docs * 3), n_amplo)
        base = rrf([l1, l2], b["rrf_k"], n_amplo)
        if filtrar:
            base = [x for x in base if not self._viola_extraido(x[0], e)]
        return base[: self.n]


def rodar(cfg, configs: list[str], fonte: str = "todas", limite: int | None = None) -> None:
    con = conectar()
    consultas = [(r["qid"], r["texto"]) for r in
                 con.execute("SELECT qid, texto FROM consultas WHERE ativa=1 ORDER BY qid")]
    if limite:
        consultas = consultas[:limite]
    if not consultas:
        raise SystemExit("Sem consultas. Rode `validar-teste` e `gabarito` antes.")
    fontes = ("descricao_tags", "reviews") if fonte == "todas" else (fonte,)
    h = hash_config(cfg)
    for f in fontes:
        precisa_denso = any(c != "C1" for c in configs)
        bus = Buscador(cfg, con, f, precisa_denso=precisa_denso)
        for c in configs:
            variantes = []
            if c == "C5":
                for beta in cfg["busca"]["penalidade_betas"]:
                    variantes.append((f"C5-b{beta}", lambda q, b=beta: bus.c5(q, b), {"beta": beta}))
            elif c == "C6":
                variantes.append(("C6", lambda q: bus.c6(q, True), {}))
                variantes.append(("C6r", lambda q: bus.c6(q, False), {"ablacao": "sem filtro"}))
            else:
                variantes.append((c, getattr(bus, c.lower()), {}))
            for nome, fn, extra in variantes:
                exec_id = f"{nome}|{f}"
                t0 = time.time()
                con.execute("DELETE FROM rankings WHERE exec_id=?", (exec_id,))
                for qid, texto in consultas:
                    for pos, (appid, nota, doc) in enumerate(fn(texto), 1):
                        con.execute("INSERT INTO rankings(exec_id, qid, posicao, appid, nota, evidencia) "
                                    "VALUES (?,?,?,?,?,?)", (exec_id, qid, pos, appid, nota, doc))
                params = {"config_hash": h, "modelos": cfg["modelos"], "busca": cfg["busca"], **extra,
                          "n_consultas": len(consultas)}
                con.execute("INSERT OR REPLACE INTO execucoes(exec_id, config, fonte, parametros, criado_em) "
                            "VALUES (?,?,?,?,?)", (exec_id, nome, f, json.dumps(params, ensure_ascii=False),
                                                   time.time()))
                con.commit()
                registrar_evento(con, "rodar", f"{exec_id}: {len(consultas)} consultas em {time.time() - t0:.0f}s")
