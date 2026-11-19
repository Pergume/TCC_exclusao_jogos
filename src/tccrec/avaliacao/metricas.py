"""Métricas por consulta.

* ``P@k``      — precisão: fração dos k primeiros jogos que são relevantes;
* ``nDCG@k``   — ganho cumulativo descontado normalizado, relevância binária;
* ``Hit@k``    — 1 se há ao menos um relevante entre os k primeiros;
* ``Viol@k``   — taxa de violação: fração dos k primeiros que têm o atributo,
  título ou desenvolvedora excluídos (adaptada da Violation@k do E-SENS e da
  SVR@k do SafeCRS);
* ``AnyViol@k`` — 1 se ao menos um dos k primeiros viola a exclusão.

Recall@k não é usado como métrica principal: as consultas têm dezenas a
centenas de jogos relevantes, e o recall nos k primeiros fica artificialmente
baixo (ver docs/DECISOES.md, D-020).
"""
from __future__ import annotations

import math


def metricas_consulta(ranking: list[int], relevantes: set[int], violadores: set[int], k: int) -> dict[str, float]:
    topo = ranking[:k]
    rel = [1 if a in relevantes else 0 for a in topo]
    vio = [1 if a in violadores else 0 for a in topo]
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rel))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevantes))))
    return {
        f"P@{k}": sum(rel) / k,
        f"nDCG@{k}": dcg / ideal if ideal else 0.0,
        f"Hit@{k}": float(any(rel)),
        f"Viol@{k}": sum(vio) / k,
        f"AnyViol@{k}": float(any(vio)),
    }
