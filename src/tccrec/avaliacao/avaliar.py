"""Avaliação completa: métricas por consulta, resumos e testes das hipóteses.

Saídas em ``<dados>/resultados/``:

* ``metricas_por_consulta.csv`` — uma linha por (execução, consulta);
* ``metricas_resumo.csv``       — médias e IC 95% (bootstrap) por execução e versão;
* ``metricas_por_tipo.csv``     — médias por tipo de restrição (versão "com");
* ``teste_P1.csv``, ``teste_P2.csv``, ``teste_P3.csv`` — testes de Wilcoxon;
* ``RESUMO.md``                 — tabelas prontas para a monografia.
"""
from __future__ import annotations

import pandas as pd

from ..config import dir_dados
from ..db import conectar, registrar_evento
from .estatistica import holm, ic_bootstrap, wilcoxon_pareado
from .metricas import metricas_consulta

K_PRINCIPAL = 5
BASES_P1 = ("C1", "C2", "C3")
REFERENCIA_P2 = "C3"


def tabela_por_consulta(con, ks: list[int]) -> pd.DataFrame:
    consultas = {r["qid"]: dict(r) for r in con.execute("SELECT * FROM consultas WHERE ativa=1")}
    rel: dict[str, set[int]] = {q: set() for q in consultas}
    vio: dict[str, set[int]] = {q: set() for q in consultas}
    for r in con.execute("SELECT qid, appid, relevante, viola FROM qrels"):
        if r["qid"] in consultas:
            if r["relevante"]:
                rel[r["qid"]].add(r["appid"])
            if r["viola"]:
                vio[r["qid"]].add(r["appid"])
    rankings: dict[tuple[str, str], list[int]] = {}
    for r in con.execute("SELECT exec_id, qid, appid FROM rankings ORDER BY exec_id, qid, posicao"):
        rankings.setdefault((r["exec_id"], r["qid"]), []).append(r["appid"])
    execs = {r["exec_id"]: dict(r) for r in con.execute("SELECT exec_id, config, fonte FROM execucoes")}
    linhas = []
    # Toda consulta entra em toda execução: ranking vazio (ex.: BM25 sem nenhum
    # termo em comum) conta como zero, em vez de sumir da média.
    qids_por_exec: dict[str, set[str]] = {}
    for exec_id, qid in rankings:
        qids_por_exec.setdefault(exec_id, set()).add(qid)
    for exec_id in execs:
        if exec_id in qids_por_exec:  # só execuções que rodaram neste conjunto de consultas
            for qid in consultas:
                rankings.setdefault((exec_id, qid), [])
    for (exec_id, qid), ranking in rankings.items():
        if qid not in consultas or exec_id not in execs:
            continue
        c = consultas[qid]
        ref = c["referencia_appid"]
        ranking = [a for a in ranking if a != ref]  # o jogo citado no pedido não conta
        linha = {"exec_id": exec_id, "config": execs[exec_id]["config"], "fonte": execs[exec_id]["fonte"],
                 "qid": qid, "par_id": c["par_id"], "versao": c["versao"], "tipo": c["tipo_restricao"],
                 "n_relevantes": len(rel[qid]), "n_retornados": len(ranking)}
        for k in ks:
            linha.update(metricas_consulta(ranking, rel[qid], vio[qid], k))
        linhas.append(linha)
    return pd.DataFrame(linhas)


def resumo(df: pd.DataFrame, ks: list[int], cfg) -> pd.DataFrame:
    a = cfg["avaliacao"]
    linhas = []
    metricas = [f"{m}@{k}" for k in ks for m in ("P", "nDCG", "Hit", "Viol", "AnyViol")]
    for (exec_id, versao), g in df.groupby(["exec_id", "versao"]):
        linha = {"exec_id": exec_id, "config": g["config"].iat[0], "fonte": g["fonte"].iat[0], "versao": versao,
                 "n_consultas": len(g)}
        for m in metricas:
            lo, hi = ic_bootstrap(g[m].tolist(), a["bootstrap_reamostras"], a["semente"])
            linha[m] = g[m].mean()
            linha[f"{m}_ic"] = f"[{lo:.3f}, {hi:.3f}]"
        linhas.append(linha)
    return pd.DataFrame(linhas).sort_values(["fonte", "versao", "config"])


def teste_p1(df: pd.DataFrame) -> pd.DataFrame:
    """P1: a violação com exclusão é menor que sem exclusão? (unilateral: com < sem)."""
    m = f"Viol@{K_PRINCIPAL}"
    linhas = []
    for exec_id, g in df.groupby("exec_id"):
        p = g.pivot_table(index="par_id", columns="versao", values=m).dropna()
        if {"com", "sem"} <= set(p.columns):
            r = wilcoxon_pareado(p["com"].tolist(), p["sem"].tolist(), "less")
            linhas.append({"exec_id": exec_id, "config": g["config"].iat[0], "fonte": g["fonte"].iat[0],
                           "metrica": m, "media_com": r["media_a"], "media_sem": r["media_b"],
                           "diferenca_media": r["diferenca_media"], "n_pares": r["n"], "p": r["p"]})
    out = pd.DataFrame(linhas)
    if len(out):
        out["p_holm"] = holm(out["p"].tolist())
    return out


def teste_p2(df: pd.DataFrame) -> pd.DataFrame:
    """P2: cada configuração contra C3 nas consultas "com" (violação e nDCG)."""
    com = df[df["versao"] == "com"]
    linhas = []
    for fonte, gf in com.groupby("fonte"):
        ref = gf[gf["config"] == REFERENCIA_P2].set_index("qid")
        if ref.empty:
            continue
        for metrica in (f"Viol@{K_PRINCIPAL}", f"nDCG@{K_PRINCIPAL}"):
            bloco = []
            for exec_id, g in gf.groupby("exec_id"):
                if g["config"].iat[0] == REFERENCIA_P2:
                    continue
                g = g.set_index("qid")
                qids = sorted(set(g.index) & set(ref.index))
                r = wilcoxon_pareado(g.loc[qids, metrica].tolist(), ref.loc[qids, metrica].tolist())
                bloco.append({"fonte": fonte, "metrica": metrica, "exec_id": exec_id, "config": g["config"].iat[0],
                              "media_config": r["media_a"], "media_C3": r["media_b"],
                              "diferenca_media": r["diferenca_media"], "n": r["n"], "p": r["p"]})
            for linha, pa in zip(bloco, holm([b["p"] for b in bloco])):
                linha["p_holm"] = pa
            linhas += bloco
    return pd.DataFrame(linhas)


def teste_p3(df: pd.DataFrame) -> pd.DataFrame:
    """P3: mesma configuração, fonte descricao_tags × reviews (consultas "com")."""
    m = f"Viol@{K_PRINCIPAL}"
    com = df[df["versao"] == "com"]
    linhas = []
    for config, g in com.groupby("config"):
        p = g.pivot_table(index="qid", columns="fonte", values=m).dropna()
        if {"descricao_tags", "reviews"} <= set(p.columns):
            r = wilcoxon_pareado(p["reviews"].tolist(), p["descricao_tags"].tolist())
            linhas.append({"config": config, "metrica": m, "media_reviews": r["media_a"],
                           "media_descricao_tags": r["media_b"], "diferenca_media": r["diferenca_media"],
                           "n": r["n"], "p": r["p"]})
    out = pd.DataFrame(linhas)
    if len(out):
        out["p_holm"] = holm(out["p"].tolist())
    return out


def _md(df: pd.DataFrame, colunas: list[str]) -> str:
    if df.empty:
        return "_(sem dados)_\n"
    d = df[colunas].copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda x: f"{x:.3f}")
    linhas = ["| " + " | ".join(colunas) + " |", "|" + "---|" * len(colunas)]
    linhas += ["| " + " | ".join(str(v) for v in row) + " |" for row in d.values]
    return "\n".join(linhas) + "\n"


def avaliar(cfg) -> dict:
    con = conectar()
    ks = cfg["busca"]["k_avaliacao"]
    df = tabela_por_consulta(con, ks)
    if df.empty:
        raise SystemExit("Sem rankings. Rode `python -m tccrec rodar` antes.")
    pasta = dir_dados() / "resultados"
    pasta.mkdir(exist_ok=True)
    df.to_csv(pasta / "metricas_por_consulta.csv", index=False)
    res = resumo(df, ks, cfg)
    res.to_csv(pasta / "metricas_resumo.csv", index=False)
    tipo = (df[df["versao"] == "com"].groupby(["fonte", "config", "tipo"])[[f"Viol@{K_PRINCIPAL}", f"nDCG@{K_PRINCIPAL}"]]
            .mean().reset_index())
    tipo.to_csv(pasta / "metricas_por_tipo.csv", index=False)
    p1, p2, p3 = teste_p1(df), teste_p2(df), teste_p3(df)
    p1.to_csv(pasta / "teste_P1.csv", index=False)
    p2.to_csv(pasta / "teste_P2.csv", index=False)
    p3.to_csv(pasta / "teste_P3.csv", index=False)
    k = K_PRINCIPAL
    md = [f"# Resultados (k = {k})\n",
          "Gerado por `python -m tccrec avaliar`. Médias por consulta; IC 95% por bootstrap no CSV de resumo.\n",
          "## Resumo por execução\n",
          _md(res, ["config", "fonte", "versao", "n_consultas", f"P@{k}", f"nDCG@{k}", f"Viol@{k}", f"AnyViol@{k}"]),
          "## P1 — a violação cai quando o pedido tem a exclusão? (Wilcoxon unilateral, com < sem)\n",
          _md(p1, ["config", "fonte", "media_com", "media_sem", "diferenca_media", "n_pares", "p", "p_holm"]),
          "## P2 — cada configuração contra C3, consultas com exclusão (Wilcoxon bilateral, Holm)\n",
          _md(p2, ["fonte", "metrica", "config", "media_config", "media_C3", "diferenca_media", "p", "p_holm"]),
          "## P3 — reviews × descrições e tags, consultas com exclusão\n",
          _md(p3, ["config", "media_reviews", "media_descricao_tags", "diferenca_media", "n", "p", "p_holm"]),
          f"## Violação@{k} por tipo de restrição (consultas com exclusão)\n",
          _md(tipo, ["fonte", "config", "tipo", f"Viol@{k}", f"nDCG@{k}"])]
    (pasta / "RESUMO.md").write_text("\n".join(md), encoding="utf-8")
    registrar_evento(con, "avaliar", f"avaliação concluída: {df['exec_id'].nunique()} execuções", pasta=str(pasta))
    return {"execucoes": int(df["exec_id"].nunique()), "pasta": str(pasta)}
