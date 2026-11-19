"""Teste opcional dos backends reais (Hugging Face) com modelos pequenos.

Roda só com ``TCC_TESTAR_MODELOS=1`` (baixa ~1,5 GB de modelos). Verifica que
sentence-transformers, CrossEncoder, transformers e ChromaDB funcionam juntos
com o código do projeto — os mesmos caminhos usados no Colab com os modelos
maiores.
"""
import os

import pytest

from tccrec.base.preparar import preparar_base
from tccrec.busca.configuracoes import rodar
from tccrec.busca.indices import indexar
from tccrec.teste.gabarito import gerar_gabarito
from tccrec.teste.validar import validar_conjunto

pytestmark = pytest.mark.skipif(os.environ.get("TCC_TESTAR_MODELOS") != "1",
                                reason="defina TCC_TESTAR_MODELOS=1 para testar os modelos reais")


def test_backends_reais(cfg, base_sintetica):
    cfg["modelos"].update({
        "embedding_backend": "sentence-transformers", "reranker_backend": "sentence-transformers",
        "llm_backend": "transformers",
        "embedding": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "reranker": "cross-encoder/ms-marco-MiniLM-L6-v2",
        "llm": "Qwen/Qwen2.5-0.5B-Instruct", "llm_max_novos_tokens": 120,
    })
    con = base_sintetica
    preparar_base(cfg)
    validar_conjunto(cfg)
    gerar_gabarito(cfg)
    indexar(cfg)
    rodar(cfg, ["C1", "C2", "C3", "C4", "C5", "C6"], "descricao_tags", limite=2)
    n = con.execute("SELECT COUNT(DISTINCT exec_id) FROM rankings").fetchone()[0]
    assert n >= 7  # C1, C2, C3, C4, C5-b0.5, C6, C6r
    validos = con.execute("SELECT SUM(valido), COUNT(*) FROM llm_cache").fetchone()
    print("saídas válidas do LLM:", validos)
    assert validos[1] > 0
