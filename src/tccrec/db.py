"""Banco SQLite do projeto: esquema, conexão e registros de auditoria.

Tudo o que é coletado, derivado ou descartado fica registrado aqui, para que a
monografia possa documentar a base com números exatos:

* ``eventos``: log de execução (avisos, erros, resumos de etapas);
* ``exclusoes``: cada jogo, review, documento ou par de consultas descartado,
  com a etapa e o motivo;
* ``etapas_coleta``: checkpoint por jogo e etapa, para retomar a coleta.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import caminho_db

ESQUEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS candidatos (
    appid INTEGER PRIMARY KEY,
    nome TEXT,
    pagina_steamspy INTEGER,
    posicao_steamspy INTEGER,
    donos TEXT,
    positivas INTEGER,
    negativas INTEGER,
    coletado_em REAL,
    origem TEXT,                 -- steamspy | mais_vendidos_br | ambos
    posicao_mais_vendidos_br INTEGER
);

CREATE TABLE IF NOT EXISTS reviews_resumo (
    appid INTEGER PRIMARY KEY,
    total_reviews INTEGER,
    total_positivas INTEGER,
    total_negativas INTEGER,
    review_score_desc TEXT,
    coletado_em REAL
);

CREATE TABLE IF NOT EXISTS reviews (
    recommendationid TEXT PRIMARY KEY,
    appid INTEGER NOT NULL,
    ordem INTEGER,               -- posição na ordenação por utilidade da Steam
    texto TEXT,
    idioma TEXT,
    recomendado INTEGER,         -- voted_up
    votos_util INTEGER,
    votos_engracado INTEGER,
    weighted_vote_score REAL,
    criado_em INTEGER,
    horas_no_review REAL,
    acesso_antecipado INTEGER
);
CREATE INDEX IF NOT EXISTS ix_reviews_appid ON reviews(appid);

CREATE TABLE IF NOT EXISTS jogos (
    appid INTEGER PRIMARY KEY,
    nome TEXT,
    tipo TEXT,
    descricao_curta TEXT,
    sobre TEXT,
    generos TEXT,                -- JSON (descrições em PT-BR quando a loja oferece)
    categorias TEXT,             -- JSON
    descritores_conteudo TEXT,   -- JSON (ids)
    idiomas_suportados TEXT,
    data_lancamento TEXT,
    desenvolvedores TEXT,        -- JSON
    publicadoras TEXT,           -- JSON
    gratuito INTEGER,
    coletado_em REAL
);

CREATE TABLE IF NOT EXISTS tags (
    appid INTEGER NOT NULL,
    tag TEXT NOT NULL,
    votos INTEGER,
    posicao INTEGER,             -- 1 = mais votada
    PRIMARY KEY (appid, tag)
);
CREATE INDEX IF NOT EXISTS ix_tags_tag ON tags(tag);

-- Jogos que compõem a base final (após todos os filtros).
CREATE TABLE IF NOT EXISTS selecionados (
    appid INTEGER PRIMARY KEY,
    posicao INTEGER,             -- ordem por número de reviews PT-BR
    selecionado_em REAL
);

-- Checkpoint da coleta: uma linha por (etapa, appid).
CREATE TABLE IF NOT EXISTS etapas_coleta (
    etapa TEXT NOT NULL,
    appid INTEGER NOT NULL,
    status TEXT NOT NULL,        -- ok | erro | vazio
    tentativas INTEGER DEFAULT 0,
    detalhe TEXT,
    atualizado_em REAL,
    PRIMARY KEY (etapa, appid)
);

CREATE TABLE IF NOT EXISTS exclusoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entidade TEXT NOT NULL,      -- jogo | review | documento | par | consulta
    entidade_id TEXT NOT NULL,
    etapa TEXT NOT NULL,
    motivo TEXT NOT NULL,
    detalhes TEXT,
    criado_em REAL
);
CREATE INDEX IF NOT EXISTS ix_exclusoes ON exclusoes(entidade, etapa, motivo);

CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nivel TEXT NOT NULL,         -- info | aviso | erro
    etapa TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    detalhes TEXT,
    criado_em REAL
);

-- Documentos indexáveis (gerados por `preparar-base`).
CREATE TABLE IF NOT EXISTS documentos (
    doc_id TEXT PRIMARY KEY,     -- "A:<appid>" ou "R:<recommendationid>"
    appid INTEGER NOT NULL,
    fonte TEXT NOT NULL,         -- descricao_tags | reviews
    texto TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_documentos_fonte ON documentos(fonte, appid);

-- Conjunto de teste e gabarito (gerados por `validar-teste` / `gabarito`).
CREATE TABLE IF NOT EXISTS consultas (
    qid TEXT PRIMARY KEY,        -- "<par>-com" | "<par>-sem"
    par_id TEXT NOT NULL,
    versao TEXT NOT NULL,        -- com | sem
    texto TEXT NOT NULL,
    tipo_restricao TEXT NOT NULL,
    referencia_appid INTEGER,
    ativa INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS qrels (
    qid TEXT NOT NULL,
    appid INTEGER NOT NULL,
    relevante INTEGER NOT NULL,  -- satisfaz a parte positiva (e, na versão "com", não viola)
    positivo INTEGER NOT NULL,   -- satisfaz a parte positiva
    viola INTEGER NOT NULL,      -- tem atributo/título excluído do par
    PRIMARY KEY (qid, appid)
);

-- Saídas das configurações (rankings).
CREATE TABLE IF NOT EXISTS execucoes (
    exec_id TEXT PRIMARY KEY,
    config TEXT NOT NULL,        -- C1..C6 (+ variante)
    fonte TEXT NOT NULL,
    parametros TEXT,             -- JSON
    criado_em REAL
);
CREATE TABLE IF NOT EXISTS rankings (
    exec_id TEXT NOT NULL,
    qid TEXT NOT NULL,
    posicao INTEGER NOT NULL,
    appid INTEGER NOT NULL,
    nota REAL,
    evidencia TEXT,              -- doc_id que gerou a nota (útil na fonte reviews)
    PRIMARY KEY (exec_id, qid, posicao)
);

-- Saídas do LLM (cache determinístico e auditável).
CREATE TABLE IF NOT EXISTS llm_cache (
    chave TEXT PRIMARY KEY,      -- sha1(modelo + tarefa + prompt)
    modelo TEXT,
    tarefa TEXT,
    entrada TEXT,
    saida_bruta TEXT,
    saida_json TEXT,
    valido INTEGER,
    criado_em REAL
);
"""

ESQUEMA_BRUTO = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS respostas (
    fonte TEXT NOT NULL,
    chave TEXT NOT NULL,
    url TEXT,
    status INTEGER,
    corpo BLOB,                  -- JSON comprimido (zlib)
    coletado_em REAL,
    PRIMARY KEY (fonte, chave)
);
"""


def conectar(caminho: str | Path | None = None) -> sqlite3.Connection:
    caminho = Path(caminho or caminho_db())
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho, timeout=60)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    _migrar(con)
    return con


def _migrar(con: sqlite3.Connection) -> None:
    """Acrescenta colunas novas a bancos criados por versões anteriores."""
    colunas = {r[1] for r in con.execute("PRAGMA table_info(candidatos)")}
    for nome, tipo in (("origem", "TEXT"), ("posicao_mais_vendidos_br", "INTEGER")):
        if nome not in colunas:
            con.execute(f"ALTER TABLE candidatos ADD COLUMN {nome} {tipo}")
    con.commit()


def conectar_bruto(caminho: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(caminho, timeout=60)
    con.executescript(ESQUEMA_BRUTO)
    return con


def _j(x: Any) -> str | None:
    if x is None:
        return None
    return json.dumps(x, ensure_ascii=False, default=str)


def registrar_evento(con: sqlite3.Connection, etapa: str, mensagem: str, nivel: str = "info", **detalhes) -> None:
    con.execute(
        "INSERT INTO eventos(nivel, etapa, mensagem, detalhes, criado_em) VALUES (?,?,?,?,?)",
        (nivel, etapa, mensagem, _j(detalhes) if detalhes else None, time.time()),
    )
    con.commit()
    print(f"[{nivel}] {etapa}: {mensagem}" + (f" {detalhes}" if detalhes and nivel != "info" else ""), flush=True)


def registrar_exclusao(con: sqlite3.Connection, entidade: str, entidade_id: Any, etapa: str, motivo: str,
                       commit: bool = True, **detalhes) -> None:
    con.execute(
        "INSERT INTO exclusoes(entidade, entidade_id, etapa, motivo, detalhes, criado_em) VALUES (?,?,?,?,?,?)",
        (entidade, str(entidade_id), etapa, motivo, _j(detalhes) if detalhes else None, time.time()),
    )
    if commit:
        con.commit()


def limpar_exclusoes(con: sqlite3.Connection, etapa: str) -> None:
    """Remove exclusões de uma etapa antes de reprocessá-la (evita duplicatas)."""
    con.execute("DELETE FROM exclusoes WHERE etapa = ?", (etapa,))
    con.commit()


def marcar_etapa(con: sqlite3.Connection, etapa: str, appid: int, status: str, detalhe: str | None = None) -> None:
    con.execute(
        """INSERT INTO etapas_coleta(etapa, appid, status, tentativas, detalhe, atualizado_em)
           VALUES (?,?,?,1,?,?)
           ON CONFLICT(etapa, appid) DO UPDATE SET status=excluded.status,
               tentativas=etapas_coleta.tentativas+1, detalhe=excluded.detalhe, atualizado_em=excluded.atualizado_em""",
        (etapa, appid, status, detalhe, time.time()),
    )


def pendentes(con: sqlite3.Connection, etapa: str, appids: Iterable[int], refazer_erros: bool = True) -> list[int]:
    feitos = {
        r["appid"]
        for r in con.execute(
            "SELECT appid, status FROM etapas_coleta WHERE etapa = ?" + (" AND status != 'erro'" if refazer_erros else ""),
            (etapa,),
        )
    }
    return [a for a in appids if a not in feitos]


@contextmanager
def transacao(con: sqlite3.Connection):
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
