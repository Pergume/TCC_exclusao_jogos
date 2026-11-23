"""Teste de ponta a ponta com a base sintética e backends simulados."""
import json

import pandas as pd

from tccrec.avaliacao.avaliar import avaliar
from tccrec.avaliacao.manual import concordancia, exportar_amostra
from tccrec.base.preparar import preparar_base
from tccrec.busca.configuracoes import rodar
from tccrec.busca.indices import indexar
from tccrec.coleta import steam
from tccrec.config import dir_dados
from tccrec.relatorios import exportar_csv, gerar_relatorios
from tccrec.teste.gabarito import gerar_gabarito
from tccrec.teste.validar import validar_conjunto


def test_pipeline_completo(cfg, base_sintetica):
    con = base_sintetica
    resumo = preparar_base(cfg)
    assert resumo["documentos_fonte_A"] == 60
    # "gg" é curta e a review em inglês não parece português: ambas são excluídas
    motivos = dict(con.execute("SELECT motivo, COUNT(*) FROM exclusoes WHERE entidade='review' GROUP BY motivo"))
    assert motivos["review_curta"] == 60 and motivos["review_nao_parece_portugues"] == 60

    linhas = {l["par"]: l for l in validar_conjunto(cfg)}
    assert linhas["T04"]["status"] == "descartado" and linhas["T04"]["motivo"] == "erro_de_resolucao"
    assert linhas["T03"]["positivos_violadores"] == 3  # Medo 50, 51, 52
    mantidos = [p for p, l in linhas.items() if l["status"] == "mantido"]
    assert len(mantidos) >= 2

    g = gerar_gabarito(cfg)
    assert g["consultas"] == 2 * len(mantidos)
    # na versão "com", nenhum relevante viola; o jogo de referência nunca é relevante
    assert con.execute("SELECT COUNT(*) FROM qrels WHERE qid LIKE '%-com' AND relevante=1 AND viola=1").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM qrels WHERE appid=1 AND qid LIKE 'T01%' AND relevante=1").fetchone()[0] == 0

    indexar(cfg)
    rodar(cfg, ["C1", "C2", "C3", "C4", "C5", "C6"], "todas")
    execs = {r[0] for r in con.execute("SELECT exec_id FROM execucoes")}
    assert {"C1|descricao_tags", "C6r|reviews", "C5-b0.5|descricao_tags"} <= execs
    # C6 com filtro nunca devolve jogo com tag que o LLM mandou excluir
    assert con.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] > 0

    out = avaliar(cfg)
    pasta = dir_dados() / "resultados"
    df = pd.read_csv(pasta / "metricas_por_consulta.csv")
    assert set(df["versao"]) == {"com", "sem"}
    assert (pasta / "RESUMO.md").exists() and out["execucoes"] == len(execs)
    assert len(pd.read_csv(pasta / "teste_P1.csv")) > 0

    caminho = exportar_amostra(cfg)
    am = pd.read_csv(caminho)
    assert "auto_viola" not in am.columns  # anotação às cegas
    am["manual_viola"] = "n"
    am["manual_relevante"] = "s"
    am.to_csv(caminho, index=False)
    k = concordancia(cfg, caminho)
    assert k["viola"]["n"] == len(am)

    gerar_relatorios(cfg)
    exportar_csv(cfg)
    assert (dir_dados() / "relatorios" / "EXCLUSOES.md").exists()
    assert (dir_dados() / "export" / "exclusoes.csv").exists()


def test_selecao_registra_exclusoes(cfg, base_sintetica):
    con = base_sintetica
    con.execute("UPDATE jogos SET tipo='dlc' WHERE appid=2")
    con.execute("UPDATE jogos SET descritores_conteudo=? WHERE appid=3", (json.dumps([3]),))
    con.execute("DELETE FROM tags WHERE appid=4")
    con.execute("DELETE FROM jogos WHERE appid=5")
    con.execute("UPDATE reviews_resumo SET total_reviews=5 WHERE appid=6")
    con.commit()
    cfg["coleta"]["max_jogos"] = 50
    n = steam.selecionar(cfg)
    motivos = {r[0]: r[1] for r in con.execute("SELECT entidade_id, motivo FROM exclusoes "
                                               "WHERE etapa IN ('selecao','pre_selecao')")}
    assert motivos["2"] == "nao_e_jogo"
    assert motivos["3"] == "conteudo_adulto_explicito"
    assert motivos["4"] == "sem_tags"
    assert motivos["5"] == "indisponivel_na_loja_br"
    assert motivos["6"] == "poucas_reviews_ptbr"
    assert n == 50
