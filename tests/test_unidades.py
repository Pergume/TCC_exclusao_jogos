import math

from tccrec.avaliacao.estatistica import holm, wilcoxon_pareado
from tccrec.avaliacao.metricas import metricas_consulta
from tccrec.base.texto import html_para_texto, proporcao_portugues, tokenizar
from tccrec.busca.configuracoes import agregar_por_jogo, rrf
from tccrec.busca.modelos import extrair_json
from tccrec.busca.prompts import validar_extrair, validar_separar
from tccrec.coleta.steam import _RE_TAGS
from tccrec.teste.conjunto import normalizar_nome


def test_tokenizar_mantem_negacao():
    toks = tokenizar("Jogos como Hollow Knight, mas SEM pixel art e que não seja difícil")
    assert "sem" in toks and "nao" in toks
    assert "pixel" in toks and "dificil" in toks
    assert "como" not in toks and "e" not in toks


def test_portugues_e_html():
    assert proporcao_portugues("O jogo é muito bom e não tem nada de difícil") > 0.3
    assert proporcao_portugues("This game is really good and fun") < 0.06
    assert html_para_texto("<p>Olá&nbsp;<b>mundo</b></p>") == "Olá mundo"


def test_normalizar_nome():
    assert normalizar_nome("DARK SOULS™ III") == "dark souls iii"
    assert normalizar_nome("Baldur's Gate 3") == "baldur s gate 3"


def test_metricas_consulta():
    m = metricas_consulta([1, 2, 3, 4, 5], relevantes={1, 3}, violadores={2, 3}, k=5)
    assert m["P@5"] == 0.4
    assert m["Viol@5"] == 0.4 and m["AnyViol@5"] == 1.0
    ideal = 1 + 1 / math.log2(3)
    assert abs(m["nDCG@5"] - (1 + 1 / math.log2(4)) / ideal) < 1e-9
    vazio = metricas_consulta([9], set(), set(), 5)
    assert vazio["nDCG@5"] == 0.0 and vazio["Viol@5"] == 0.0


def test_estatistica():
    assert holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]
    r = wilcoxon_pareado([0, 0, 0], [0, 0, 0])
    assert r["p"] == 1.0
    r = wilcoxon_pareado([0.0] * 10, [0.4] * 10, "less")
    assert r["p"] < 0.01


def test_agregacao_e_rrf():
    hits = [("R:1", 10, 0.9), ("R:2", 10, 0.5), ("R:3", 20, 0.7)]
    assert agregar_por_jogo(hits, 5) == [(10, 0.9, "R:1"), (20, 0.7, "R:3")]
    fundido = rrf([[(1, 0, "a"), (2, 0, "b")], [(2, 0, "c"), (3, 0, "d")]], 60, 10)
    assert fundido[0][0] == 2


def test_extrair_json_e_validacao():
    bruto = 'Claro! {"consulta_positiva": "jogos de terror", "excluir_tags": ["First-Person", "Inventada"], ' \
            '"excluir_titulos": [], "excluir_desenvolvedores": []} fim'
    d = extrair_json(bruto)
    norm, ok = validar_extrair(d, "pedido", {"First-Person", "Horror"})
    assert ok and norm["excluir_tags"] == ["First-Person"] and norm["tags_invalidas"] == ["Inventada"]
    norm, ok = validar_separar(None, "pedido original")
    assert not ok and norm == {"consulta_positiva": "pedido original", "excluir": None}


def test_regex_tags_pagina_loja():
    html = 'x InitAppTagModal( 10, [{"tagid":1,"name":"Metroidvania","count":50}], "a", 1 ) y'
    import json
    assert json.loads(_RE_TAGS.search(html).group(1))[0]["name"] == "Metroidvania"
