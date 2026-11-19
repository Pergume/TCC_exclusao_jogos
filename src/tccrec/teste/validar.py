"""Valida o conjunto de teste contra a base e registra os pares descartados."""
from __future__ import annotations

import csv

from ..config import dir_dados
from ..db import conectar, limpar_exclusoes, registrar_evento, registrar_exclusao
from .conjunto import Base, carregar_conjunto, estatisticas_par, motivo_descartar, resolver_par


def validar_conjunto(cfg) -> list[dict]:
    con = conectar()
    limpar_exclusoes(con, "validar_teste")
    atributos, pares = carregar_conjunto(cfg)
    base = Base(con, cfg["base"]["tags_top_n"])
    if not base.appids:
        raise SystemExit("Base vazia. Rode a coleta antes.")
    linhas = []
    for par in pares:
        resolver_par(par, atributos, base)
        est = estatisticas_par(par, base)
        motivo = motivo_descartar(par, est, cfg)
        linha = {"par": par.id, "tipo": par.tipo, "com": par.com, "referencia": base.nome.get(par.ref_appid, ""),
                 **est, "tags_excluidas": "; ".join(par.tags_excluidas),
                 "titulos_excluidos": "; ".join(sorted(base.nome[a] for a in par.appids_excluidos))[:300],
                 "status": "descartado" if motivo else "mantido", "motivo": motivo or "",
                 "problemas": " | ".join(par.problemas)}
        linhas.append(linha)
        if motivo:
            registrar_exclusao(con, "par", par.id, "validar_teste", motivo, commit=False, **est,
                               problemas=par.problemas)
    con.commit()
    saida = dir_dados() / "validacao_conjunto_teste.csv"
    with open(saida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)
    mantidos = sum(1 for l in linhas if l["status"] == "mantido")
    por_tipo = {}
    for l in linhas:
        if l["status"] == "mantido":
            por_tipo[l["tipo"]] = por_tipo.get(l["tipo"], 0) + 1
    registrar_evento(con, "validar_teste", f"{mantidos} de {len(linhas)} pares mantidos", por_tipo=por_tipo,
                     arquivo=str(saida))
    for l in linhas:
        flag = "OK " if l["status"] == "mantido" else "XX "
        print(f"{flag}{l['par']} pos={l['positivos']:4d} conf={l['proporcao_conflito']:.2f} "
              f"rel_com={l['relevantes_com']:4d} {l['motivo']} {l['problemas']}")
    return linhas
