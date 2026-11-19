"""Gera as consultas e o gabarito (qrels) dos pares mantidos.

Para cada par mantido são criadas duas consultas (``<par>-com`` e
``<par>-sem``). O gabarito guarda, para cada jogo positivo ou violador, as
marcações ``positivo``, ``viola`` e ``relevante``; jogos ausentes do gabarito
são não relevantes e não violadores.
"""
from __future__ import annotations

import csv

from ..config import dir_dados
from ..db import conectar, registrar_evento
from .conjunto import Base, carregar_conjunto, estatisticas_par, julgar, motivo_descartar, resolver_par


def gerar_gabarito(cfg) -> dict:
    con = conectar()
    atributos, pares = carregar_conjunto(cfg)
    base = Base(con, cfg["base"]["tags_top_n"])
    con.execute("DELETE FROM consultas")
    con.execute("DELETE FROM qrels")
    n_pares = n_qrels = 0
    for par in pares:
        resolver_par(par, atributos, base)
        if motivo_descartar(par, estatisticas_par(par, base), cfg):
            continue
        n_pares += 1
        for versao, texto in (("com", par.com), ("sem", par.sem)):
            qid = f"{par.id}-{versao}"
            con.execute("""INSERT INTO consultas(qid, par_id, versao, texto, tipo_restricao, referencia_appid)
                           VALUES (?,?,?,?,?,?)""", (qid, par.id, versao, texto, par.tipo, par.ref_appid))
            for appid in base.appids:
                positivo, viola = julgar(par, base, appid)
                if not (positivo or viola):
                    continue
                relevante = positivo and (versao == "sem" or not viola)
                con.execute("INSERT INTO qrels(qid, appid, relevante, positivo, viola) VALUES (?,?,?,?,?)",
                            (qid, appid, int(relevante), int(positivo), int(viola)))
                n_qrels += 1
    con.commit()
    # exporta em formato TREC (qid 0 docid relevância) para uso externo
    saida = dir_dados() / "qrels.tsv"
    with open(saida, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["qid", "appid", "relevante", "positivo", "viola"])
        for r in con.execute("SELECT qid, appid, relevante, positivo, viola FROM qrels ORDER BY qid, appid"):
            w.writerow(list(r))
    resumo = {"pares": n_pares, "consultas": 2 * n_pares, "linhas_qrels": n_qrels, "arquivo": str(saida)}
    registrar_evento(con, "gabarito", "gabarito gerado", **resumo)
    return resumo
