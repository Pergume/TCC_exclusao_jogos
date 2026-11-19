"""Gera os documentos indexáveis das duas fontes de texto.

* Fonte A — ``descricao_tags``: um documento por jogo com nome, gêneros (PT-BR),
  tags de usuário (nome original em inglês + tradução própria em PT-BR),
  descrição curta e trecho do "sobre o jogo".
* Fonte B — ``reviews``: um documento por review em PT-BR (até N por jogo, as
  mais úteis segundo a Steam). A nota do jogo é agregada depois (MaxP).

Toda review descartada é registrada em ``exclusoes`` com o motivo.
"""
from __future__ import annotations

import csv
import json

from ..config import MANUAL
from ..db import conectar, limpar_exclusoes, registrar_evento, registrar_exclusao
from .texto import html_para_texto, limpar_review, proporcao_letras, proporcao_portugues


def carregar_traducoes_tags() -> dict[str, str]:
    caminho = MANUAL / "tags_ptbr.csv"
    if not caminho.exists():
        return {}
    with open(caminho, encoding="utf-8") as f:
        return {r["tag"].strip(): r["ptbr"].strip() for r in csv.DictReader(f) if r.get("ptbr")}


def tags_presentes(con, top_n: int) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for r in con.execute("SELECT appid, tag FROM tags WHERE posicao <= ? ORDER BY appid, posicao", (top_n,)):
        out.setdefault(r["appid"], []).append(r["tag"])
    return out


def documento_descricao_tags(j, tags: list[str], traducoes: dict[str, str], sobre_max: int) -> str:
    generos = ", ".join(json.loads(j["generos"] or "[]"))
    tags_txt = "; ".join(f"{t} ({traducoes[t]})" if t in traducoes else t for t in tags)
    sobre = html_para_texto(j["sobre"])
    if len(sobre) > sobre_max:
        sobre = sobre[:sobre_max].rsplit(" ", 1)[0] + "…"
    partes = [
        f"{j['nome']}.",
        f"Gêneros: {generos}." if generos else "",
        f"Tags: {tags_txt}." if tags_txt else "",
        html_para_texto(j["descricao_curta"]),
        sobre,
    ]
    return " ".join(p for p in partes if p)


def preparar_base(cfg) -> dict:
    con = conectar()
    b = cfg["base"]
    # Normaliza tags com espaço sobrando de coletas antigas (P-009).
    con.execute("""DELETE FROM tags WHERE tag != trim(tag) AND EXISTS
                   (SELECT 1 FROM tags t2 WHERE t2.appid = tags.appid AND t2.tag = trim(tags.tag))""")
    con.execute("UPDATE tags SET tag = trim(tag) WHERE tag != trim(tag)")
    limpar_exclusoes(con, "preparar_base")
    con.execute("DELETE FROM documentos")
    traducoes = carregar_traducoes_tags()
    selecionados = [r[0] for r in con.execute("SELECT appid FROM selecionados ORDER BY posicao")]
    if not selecionados:
        raise SystemExit("Nenhum jogo selecionado. Rode `python -m tccrec coletar` antes.")
    tags = tags_presentes(con, b["tags_top_n"])
    jogos = {r["appid"]: r for r in con.execute("SELECT * FROM jogos")}

    # Fonte A
    n_a = 0
    for appid in selecionados:
        texto = documento_descricao_tags(jogos[appid], tags.get(appid, []), traducoes, b["sobre_max_caracteres"])
        con.execute("INSERT INTO documentos(doc_id, appid, fonte, texto) VALUES (?,?,?,?)",
                    (f"A:{appid}", appid, "descricao_tags", texto))
        n_a += 1

    # Fonte B
    contagem = {"aceitas": 0}
    sel = set(selecionados)
    por_jogo: dict[int, int] = {}
    vistos: dict[int, set[str]] = {}
    for r in con.execute("SELECT recommendationid, appid, texto FROM reviews ORDER BY appid, ordem"):
        if r["appid"] not in sel:
            continue
        rid, appid = r["recommendationid"], r["appid"]
        texto = limpar_review(r["texto"])
        motivo = None
        if len(texto) < b["review_min_caracteres"]:
            motivo = "review_curta"
        elif proporcao_letras(texto) < 0.5:
            motivo = "review_simbolos_ou_arte_ascii"
        elif proporcao_portugues(texto) < b["review_min_proporcao_pt"]:
            motivo = "review_nao_parece_portugues"
        elif texto.lower() in vistos.setdefault(appid, set()):
            motivo = "review_duplicada_no_jogo"
        elif por_jogo.get(appid, 0) >= b["reviews_indexadas_por_jogo"]:
            motivo = "review_alem_do_limite_por_jogo"
        if motivo:
            contagem[motivo] = contagem.get(motivo, 0) + 1
            if motivo != "review_alem_do_limite_por_jogo":  # o excedente é só contado, não listado
                registrar_exclusao(con, "review", rid, "preparar_base", motivo, commit=False, appid=appid)
            continue
        vistos[appid].add(texto.lower())
        if len(texto) > b["review_max_caracteres"]:
            texto = texto[: b["review_max_caracteres"]].rsplit(" ", 1)[0] + "…"
        con.execute("INSERT INTO documentos(doc_id, appid, fonte, texto) VALUES (?,?,?,?)",
                    (f"R:{rid}", appid, "reviews", texto))
        por_jogo[appid] = por_jogo.get(appid, 0) + 1
        contagem["aceitas"] += 1
    sem_reviews = [a for a in selecionados if not por_jogo.get(a)]
    for a in sem_reviews:
        registrar_exclusao(con, "documento", a, "preparar_base", "jogo_sem_reviews_validas_na_fonte_reviews",
                           commit=False)
    con.commit()
    resumo = {"documentos_fonte_A": n_a, "reviews": contagem, "jogos_sem_reviews_validas": len(sem_reviews),
              "tags_com_traducao": sum(1 for t in {t for ts in tags.values() for t in ts} if t in traducoes)}
    registrar_evento(con, "preparar_base", "documentos gerados", **resumo)
    return resumo
