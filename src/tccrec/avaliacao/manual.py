"""Conferência manual do gabarito automático.

``exportar_amostra`` sorteia ~50 pares (consulta, jogo) do top-5 de uma
execução, estratificados por violação automática, e gera dois arquivos:

* ``amostra_manual.csv`` — para anotar às cegas (sem os rótulos automáticos);
* ``amostra_manual_chave.csv`` — os rótulos automáticos, para comparar depois.

``concordancia`` lê o CSV anotado (colunas ``manual_viola`` e
``manual_relevante`` com s/n) e calcula concordância e kappa de Cohen.
"""
from __future__ import annotations

import json

import pandas as pd

from ..config import dir_dados
from ..db import conectar, registrar_evento


def exportar_amostra(cfg, exec_id: str | None = None) -> str:
    con = conectar()
    exec_id = exec_id or "C3|descricao_tags"
    n = cfg["avaliacao"]["amostra_manual_tamanho"]
    q = pd.read_sql_query(
        """SELECT r.qid, c.texto AS consulta, c.tipo_restricao AS tipo, r.posicao, r.appid, j.nome AS jogo,
                  COALESCE(g.relevante, 0) AS auto_relevante, COALESCE(g.viola, 0) AS auto_viola,
                  c.referencia_appid
           FROM rankings r JOIN consultas c USING(qid) JOIN jogos j ON j.appid = r.appid
           LEFT JOIN qrels g ON g.qid = r.qid AND g.appid = r.appid
           WHERE r.exec_id = ? AND c.versao = 'com' AND r.posicao <= 6""", con, params=(exec_id,))
    q = q[q["appid"] != q["referencia_appid"]]
    if q.empty:
        raise SystemExit(f"Sem rankings para {exec_id}.")
    semente = cfg["avaliacao"]["semente"]
    metade = n // 2
    viol, nao = q[q["auto_viola"] == 1], q[q["auto_viola"] == 0]
    amostra = pd.concat([viol.sample(min(metade, len(viol)), random_state=semente),
                         nao.sample(min(n - min(metade, len(viol)), len(nao)), random_state=semente)])
    amostra = amostra.sample(frac=1, random_state=semente).reset_index(drop=True)
    tags = {}
    for r in con.execute("SELECT appid, tag FROM tags WHERE posicao <= ? ORDER BY posicao", (cfg["base"]["tags_top_n"],)):
        tags.setdefault(r[0], []).append(r[1])
    desc = dict(con.execute("SELECT appid, descricao_curta FROM jogos").fetchall())
    amostra["item"] = range(1, len(amostra) + 1)
    amostra["link_loja"] = amostra["appid"].map(lambda a: f"https://store.steampowered.com/app/{a}/")
    amostra["tags"] = amostra["appid"].map(lambda a: ", ".join(tags.get(a, [])))
    amostra["descricao_curta"] = amostra["appid"].map(lambda a: desc.get(a) or "")
    amostra["manual_viola"] = ""
    amostra["manual_relevante"] = ""
    amostra["observacao"] = ""
    pasta = dir_dados() / "manual"
    pasta.mkdir(exist_ok=True)
    cega = ["item", "qid", "consulta", "jogo", "link_loja", "descricao_curta", "tags",
            "manual_viola", "manual_relevante", "observacao"]
    amostra[cega].to_csv(pasta / "amostra_manual.csv", index=False)
    amostra[["item", "qid", "appid", "auto_viola", "auto_relevante", "tipo"]].to_csv(
        pasta / "amostra_manual_chave.csv", index=False)
    registrar_evento(con, "amostra_manual", f"{len(amostra)} itens exportados de {exec_id}", pasta=str(pasta))
    return str(pasta / "amostra_manual.csv")


def _kappa(a: list[int], b: list[int]) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def concordancia(cfg, arquivo: str) -> dict:
    anot = pd.read_csv(arquivo)
    chave = pd.read_csv(dir_dados() / "manual" / "amostra_manual_chave.csv")
    d = anot.merge(chave, on=["item", "qid"])
    sim = lambda s: 1 if str(s).strip().lower() in {"s", "sim", "1", "y", "yes", "true"} else 0
    out = {}
    for campo in ("viola", "relevante"):
        col = d[f"manual_{campo}"].astype(str).str.strip()
        validos = d[col.isin(["s", "n", "sim", "nao", "não", "1", "0", "S", "N"])]
        m = [sim(x) for x in validos[f"manual_{campo}"]]
        a = validos[f"auto_{campo}"].astype(int).tolist()
        out[campo] = {"n": len(m), "concordancia": (sum(x == y for x, y in zip(m, a)) / len(m)) if m else None,
                      "kappa": _kappa(m, a),
                      "falsos_positivos_auto": sum(1 for x, y in zip(m, a) if y == 1 and x == 0),
                      "falsos_negativos_auto": sum(1 for x, y in zip(m, a) if y == 0 and x == 1)}
    con = conectar()
    registrar_evento(con, "concordancia", "concordância calculada", **json.loads(json.dumps(out)))
    (dir_dados() / "manual" / "concordancia.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    return out
