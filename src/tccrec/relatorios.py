"""Relatórios gerados a partir do banco, para a documentação e a monografia.

* ``docs/RELATORIO_BASE.md`` — números da base (funil da coleta, tags, reviews,
  documentos, conjunto de teste);
* ``docs/EXCLUSOES.md`` — tudo o que foi descartado, por etapa e motivo;
* ``exportar_csv`` — todas as tabelas em CSV (``<dados>/export``).

Cópias dos dois relatórios também são gravadas em ``<dados>/relatorios``.
"""
from __future__ import annotations

import json
import time

import pandas as pd

from .config import RAIZ, dir_dados
from .db import conectar, registrar_evento

TABELAS = ["candidatos", "reviews_resumo", "jogos", "tags", "selecionados", "etapas_coleta", "exclusoes",
           "eventos", "consultas", "qrels", "execucoes", "llm_cache"]


def _um(con, sql: str, *p):
    r = con.execute(sql, p).fetchone()
    return r[0] if r else None


def _tabela_md(linhas: list[tuple], cab: list[str]) -> str:
    out = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    out += ["| " + " | ".join(str(x) for x in l) + " |" for l in linhas]
    return "\n".join(out)


def relatorio_base(con, cfg) -> str:
    c = cfg["coleta"]
    funil = [
        ("Candidatos (SteamSpy + mais vendidos BR)", _um(con, "SELECT COUNT(*) FROM candidatos")),
        ("— só SteamSpy", _um(con, "SELECT COUNT(*) FROM candidatos WHERE origem='steamspy'")),
        ("— só mais vendidos BR", _um(con, "SELECT COUNT(*) FROM candidatos WHERE origem='mais_vendidos_br'")),
        ("— nas duas listas", _um(con, "SELECT COUNT(*) FROM candidatos WHERE origem='ambos'")),
        (f"Após o corte de {c['min_avaliacoes_steamspy']} avaliações no SteamSpy",
         _um(con, "SELECT COUNT(*) FROM candidatos") - (_um(con, "SELECT COUNT(*) FROM exclusoes WHERE etapa='candidatos'") or 0)),
        ("Com reviews PT-BR consultadas", _um(con, "SELECT COUNT(*) FROM reviews_resumo")),
        (f"Com ≥ {c['min_reviews_ptbr']} reviews PT-BR", _um(con, "SELECT COUNT(*) FROM reviews_resumo WHERE total_reviews >= ?", c["min_reviews_ptbr"])),
        ("Pré-selecionados (detalhes e tags coletados)", _um(con, "SELECT COUNT(DISTINCT appid) FROM etapas_coleta WHERE etapa='detalhes'")),
        ("Base final (selecionados)", _um(con, "SELECT COUNT(*) FROM selecionados")),
    ]
    rv = con.execute("""SELECT COUNT(*), SUM(r.total_reviews), MIN(r.total_reviews), AVG(r.total_reviews)
                        FROM reviews_resumo r JOIN selecionados s USING(appid)""").fetchone()
    mediana = _um(con, """SELECT total_reviews FROM reviews_resumo r JOIN selecionados s USING(appid)
                          ORDER BY total_reviews LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM selecionados)""")
    docs = con.execute("SELECT fonte, COUNT(*), COUNT(DISTINCT appid), AVG(LENGTH(texto)) FROM documentos GROUP BY fonte").fetchall()
    top_tags = con.execute("""SELECT t.tag, COUNT(*) FROM tags t JOIN selecionados s USING(appid)
                              WHERE t.posicao <= ? GROUP BY t.tag ORDER BY 2 DESC LIMIT 40""",
                           (cfg["base"]["tags_top_n"],)).fetchall()
    fontes_tags = con.execute("""SELECT detalhe, COUNT(*) FROM etapas_coleta e JOIN selecionados s USING(appid)
                                 WHERE etapa='tags' GROUP BY detalhe""").fetchall()
    tipos = con.execute("SELECT tipo_restricao, COUNT(*)/2 FROM consultas GROUP BY tipo_restricao").fetchall()
    from .base.texto import html_para_texto, proporcao_portugues
    descr = [html_para_texto(r[0]) for r in con.execute(
        "SELECT j.descricao_curta FROM jogos j JOIN selecionados s USING(appid)")]
    em_pt = sum(1 for d in descr if proporcao_portugues(d) >= cfg["base"]["review_min_proporcao_pt"])
    partes = [
        "# Relatório da base",
        f"Gerado automaticamente por `python -m tccrec relatorio` em {time.strftime('%d/%m/%Y %H:%M')}. "
        "Não edite à mão: rode o comando de novo.",
        "## Funil da coleta", _tabela_md(funil, ["Etapa", "Jogos"]),
        "Os motivos de cada descarte estão em `docs/EXCLUSOES.md`.",
        "## Reviews em português dos jogos selecionados",
        _tabela_md([(rv[0], rv[1], rv[2], round(rv[3] or 0, 1), mediana)],
                   ["Jogos", "Total de reviews PT-BR na Steam", "Mínimo", "Média", "Mediana"]),
        f"Reviews baixadas: {_um(con, 'SELECT COUNT(*) FROM reviews r JOIN selecionados s USING(appid)')} "
        f"(até {c['reviews_por_jogo']} por jogo, as mais úteis segundo a Steam).",
        "## Documentos indexáveis",
        _tabela_md([(f, n, j, round(t or 0)) for f, n, j, t in docs] or [("—", 0, 0, 0)],
                   ["Fonte", "Documentos", "Jogos", "Tamanho médio (caracteres)"]),
        "## Idioma das descrições (fonte A)",
        f"Descrições curtas que parecem estar em português: {em_pt} de {len(descr)} "
        f"({(100 * em_pt / len(descr)) if descr else 0:.1f}%). As demais vieram em inglês, porque o jogo não tem "
        "localização em português (P-005). Critério: o mesmo filtro de palavras funcionais das reviews.",
        "## Fonte das tags",
        _tabela_md(fontes_tags or [("—", 0)], ["Fonte", "Jogos"]),
        "## Tags mais frequentes (top-20 de cada jogo)",
        _tabela_md(top_tags, ["Tag", "Jogos"]),
        "## Conjunto de teste",
        _tabela_md(tipos or [("—", 0)], ["Tipo de restrição", "Pares mantidos"]),
        f"Consultas: {_um(con, 'SELECT COUNT(*) FROM consultas')}; linhas de gabarito: {_um(con, 'SELECT COUNT(*) FROM qrels')}.",
    ]
    return "\n\n".join(partes) + "\n"


def relatorio_exclusoes(con) -> str:
    resumo = con.execute("""SELECT etapa, entidade, motivo, COUNT(*) FROM exclusoes
                            GROUP BY etapa, entidade, motivo ORDER BY MIN(id)""").fetchall()
    jogos = con.execute("""SELECT e.etapa, e.motivo, e.entidade_id, e.detalhes FROM exclusoes e
                           WHERE e.entidade='jogo' AND e.etapa='selecao' ORDER BY e.motivo, e.id""").fetchall()
    pares = con.execute("SELECT entidade_id, motivo, detalhes FROM exclusoes WHERE entidade='par' ORDER BY entidade_id").fetchall()
    partes = ["# Exclusões",
              f"Gerado automaticamente por `python -m tccrec relatorio` em {time.strftime('%d/%m/%Y %H:%M')}. "
              "A lista completa, item a item, está na tabela `exclusoes` do banco (e em `export/exclusoes.csv`).",
              "## Resumo por etapa e motivo", _tabela_md(resumo, ["Etapa", "Entidade", "Motivo", "Quantidade"]),
              "## Pares do conjunto de teste descartados",
              _tabela_md([(p, m, (json.loads(d or "{}").get("problemas") or "")) for p, m, d in pares] or [("—", "—", "")],
                         ["Par", "Motivo", "Problemas"]),
              "## Jogos descartados na seleção final (exceto corte por quantidade)",
              _tabela_md([(m, a, (json.loads(d or "{}").get("nome") or "")) for _, m, a, d in jogos
                          if m != "fora_do_corte_max_jogos"][:500] or [("—", "—", "")],
                         ["Motivo", "appid", "Nome"])]
    return "\n\n".join(partes) + "\n"


def gerar_relatorios(cfg) -> None:
    """Grava os relatórios só na pasta de dados (dados/relatorios), nunca no repositório."""
    con = conectar()
    destino = dir_dados() / "relatorios"
    destino.mkdir(parents=True, exist_ok=True)
    for nome, texto in (("RELATORIO_BASE.md", relatorio_base(con, cfg)), ("EXCLUSOES.md", relatorio_exclusoes(con))):
        (destino / nome).write_text(texto, encoding="utf-8")
    registrar_evento(con, "relatorio", "relatórios gerados em dados/relatorios/")


def exportar_csv(cfg) -> None:
    con = conectar()
    pasta = dir_dados() / "export"
    pasta.mkdir(exist_ok=True)
    for t in TABELAS:
        pd.read_sql_query(f"SELECT * FROM {t}", con).to_csv(pasta / f"{t}.csv", index=False)
    pd.read_sql_query("SELECT doc_id, appid, fonte, texto FROM documentos", con).to_csv(pasta / "documentos.csv", index=False)
    registrar_evento(con, "exportar", f"tabelas exportadas para {pasta}")
