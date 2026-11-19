"""Base sintética e configuração com backends simulados (sem GPU, sem rede)."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tccrec.config import carregar_config  # noqa: E402

TAGS = ["Metroidvania", "Pixel Graphics", "Difficult", "Platformer", "Horror", "First-Person", "Survival",
        "Multiplayer", "Roguelike", "Singleplayer", "2D", "Atmospheric"]
DESCRICOES = {
    "Metroidvania": "exploração de mapa interligado estilo metroidvania",
    "Pixel Graphics": "visual em pixel art",
    "Difficult": "desafio muito difícil e punitivo",
    "Platformer": "jogo de plataforma com pulos precisos",
    "Horror": "jogo de terror assustador",
    "First-Person": "em primeira pessoa",
    "Survival": "sobrevivência com recursos escassos",
    "Multiplayer": "multiplayer online com amigos",
    "Roguelike": "roguelike com morte permanente",
}

CONJUNTO = {
    "atributos": {
        "pixel_art": {"rotulo": "pixel art", "tags": ["Pixel Graphics"]},
        "dificil": {"rotulo": "difícil", "tags": ["Difficult"]},
    },
    "pares": [
        {"id": "T01", "tipo": "estilo_visual", "com": "metroidvanias sem pixel art", "sem": "metroidvanias",
         "positivo": [["Metroidvania"]], "exclusao": {"atributo": "pixel_art"},
         "referencia": {"nome": "Jogo 1", "appid": 1}},
        {"id": "T02", "tipo": "dificuldade", "com": "jogos de plataforma que não sejam difíceis",
         "sem": "jogos de plataforma", "positivo": [["Platformer"]], "exclusao": {"atributo": "dificil"}},
        {"id": "T03", "tipo": "titulo_referencia", "com": "jogos de terror que não sejam da série Medo",
         "sem": "jogos de terror", "positivo": [["Horror"]], "exclusao": {"padrao_titulo": ["medo"]}},
        {"id": "T04", "tipo": "tema", "com": "jogos de corrida sem pixel art", "sem": "jogos de corrida",
         "positivo": [["Racing"]], "exclusao": {"atributo": "pixel_art"}},
    ],
}


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TCC_DATA_DIR", str(tmp_path / "dados"))
    arq = tmp_path / "conjunto.yaml"
    arq.write_text(yaml.safe_dump(CONJUNTO, allow_unicode=True), encoding="utf-8")
    return carregar_config(sobrescrever={
        "teste": {"arquivo": str(arq), "min_positivos": 3, "min_relevantes_com": 2, "min_proporcao_conflito": 0.1,
                  "min_violadores_titulo": 1},
        "modelos": {"embedding_backend": "fake", "reranker_backend": "fake", "llm_backend": "fake"},
        "busca": {"profundidade_candidatos": 20, "rerank_profundidade": 10, "penalidade_betas": [0.5]},
        "base": {"reviews_indexadas_por_jogo": 3},
        "avaliacao": {"bootstrap_reamostras": 200, "amostra_manual_tamanho": 10},
    })


@pytest.fixture()
def base_sintetica(cfg):
    """60 jogos com tags aleatórias (semente fixa), descrições e reviews em PT-BR."""
    from tccrec.db import conectar
    rng = random.Random(7)
    con = conectar()
    for appid in range(1, 61):
        nome = f"Medo {appid}" if appid in (50, 51, 52) else f"Jogo {appid}"
        tags = rng.sample(TAGS, 6)
        if appid in (50, 51, 52):
            tags = ["Horror"] + [t for t in tags if t != "Horror"][:5]
        con.execute("INSERT INTO candidatos(appid, nome, pagina_steamspy, posicao_steamspy, positivas, negativas) "
                    "VALUES (?,?,?,?,?,?)", (appid, nome, 0, appid, 5000, 100))
        con.execute("INSERT INTO reviews_resumo(appid, total_reviews) VALUES (?,?)", (appid, 500 - appid))
        desc = ". ".join(DESCRICOES[t] for t in tags if t in DESCRICOES)
        con.execute("""INSERT INTO jogos(appid, nome, tipo, descricao_curta, sobre, generos, categorias,
                       descritores_conteudo, desenvolvedores, publicadoras) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (appid, nome, "game", desc, f"<p>{desc}</p>", json.dumps(["Ação"]), "[]", "[]",
                     json.dumps(["Estúdio X"]), json.dumps(["Editora Y"])))
        for pos, t in enumerate(tags, 1):
            con.execute("INSERT INTO tags(appid, tag, votos, posicao) VALUES (?,?,?,?)", (appid, t, 100 - pos, pos))
        textos = [f"Esse jogo é muito bom, {desc}, recomendo para quem gosta do gênero.",
                  "Não é difícil, mas é um jogo que vale muito a pena jogar com calma.",
                  "gg", "Great game, I love it so much, really fun to play every day."]
        for i, t in enumerate(textos, 1):
            con.execute("INSERT INTO reviews(recommendationid, appid, ordem, texto, idioma) VALUES (?,?,?,?,?)",
                        (f"{appid}-{i}", appid, i, t, "brazilian"))
        con.execute("INSERT INTO selecionados(appid, posicao) VALUES (?,?)", (appid, appid))
        for etapa in ("detalhes", "tags", "reviews"):
            con.execute("INSERT INTO etapas_coleta(etapa, appid, status) VALUES (?,?,?)", (etapa, appid, "ok"))
    con.commit()
    return con
