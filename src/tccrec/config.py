"""Leitura da configuração e caminhos de dados."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parents[2]
CONFIG_PADRAO = RAIZ / "config" / "config.yaml"
MANUAL = RAIZ / "data" / "manual"


def carregar_config(caminho: str | os.PathLike | None = None, sobrescrever: dict | None = None) -> dict[str, Any]:
    """Lê o YAML de configuração e aplica sobrescritas (dicionário aninhado)."""
    caminho = Path(caminho or os.environ.get("TCC_CONFIG", CONFIG_PADRAO))
    with open(caminho, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if sobrescrever:
        cfg = _mesclar(cfg, sobrescrever)
    cfg["_arquivo"] = str(caminho)
    return cfg


def _mesclar(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _mesclar(out[k], v)
        else:
            out[k] = v
    return out


def hash_config(cfg: dict) -> str:
    limpo = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return hashlib.sha1(json.dumps(limpo, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]


def dir_dados() -> Path:
    """Diretório de dados (SQLite, índices, execuções). No Colab, aponte para o Drive."""
    d = Path(os.environ.get("TCC_DATA_DIR", RAIZ / "dados"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def caminho_db() -> Path:
    return dir_dados() / "base.sqlite"


def caminho_cache_bruto() -> Path:
    return dir_dados() / "respostas_brutas.sqlite"
