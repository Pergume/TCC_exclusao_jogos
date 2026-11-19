"""Testes estatísticos pareados por consulta."""
from __future__ import annotations

import numpy as np
from scipy import stats


def wilcoxon_pareado(a: list[float], b: list[float], alternativa: str = "two-sided") -> dict:
    """Wilcoxon de postos sinalizados (método de Pratt para empates em zero).

    Devolve médias, diferença média (a − b), estatística e p-valor. Quando
    todas as diferenças são zero, p = 1.
    """
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a - b
    out = {"n": int(len(d)), "media_a": float(a.mean()) if len(a) else float("nan"),
           "media_b": float(b.mean()) if len(b) else float("nan"),
           "diferenca_media": float(d.mean()) if len(d) else float("nan")}
    if len(d) == 0 or np.allclose(d, 0):
        out.update({"estatistica": float("nan"), "p": 1.0})
        return out
    r = stats.wilcoxon(a, b, zero_method="pratt", alternative=alternativa)
    out.update({"estatistica": float(r.statistic), "p": float(r.pvalue)})
    return out


def ic_bootstrap(valores: list[float], reamostras: int = 2000, semente: int = 42, alfa: float = 0.05):
    v = np.asarray(valores, float)
    if len(v) == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(semente)
    medias = rng.choice(v, size=(reamostras, len(v)), replace=True).mean(axis=1)
    return float(np.quantile(medias, alfa / 2)), float(np.quantile(medias, 1 - alfa / 2))


def holm(pvalores: list[float]) -> list[float]:
    """Correção de Holm-Bonferroni para comparações múltiplas."""
    m = len(pvalores)
    ordem = np.argsort(pvalores)
    ajust = [0.0] * m
    maximo = 0.0
    for i, idx in enumerate(ordem):
        valor = min(1.0, (m - i) * pvalores[idx])
        maximo = max(maximo, valor)
        ajust[idx] = maximo
    return ajust
