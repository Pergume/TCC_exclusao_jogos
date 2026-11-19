"""Prompts do LLM. O MESMO modelo é usado em C5 e C6 (D-013).

Os exemplos few-shot usam atributos que NÃO aparecem no conjunto de teste,
para não vazar o gabarito para o modelo (D-018).
"""
from __future__ import annotations

SISTEMA = ("Você analisa pedidos de recomendação de jogos escritos em português. "
           "Responda somente com um objeto JSON válido, sem comentários.")

# --------------------------------------------------------------------- C5
INSTRUCAO_SEPARAR = """Separe o pedido em duas partes:
- "consulta_positiva": o que a pessoa QUER, reescrito sem a parte excluída;
- "excluir": o que a pessoa NÃO quer, em poucas palavras, ou null se não houver exclusão.

Exemplos:
PEDIDO: jogos de aventura com piratas, mas sem combate naval
{"consulta_positiva": "jogos de aventura com piratas", "excluir": "combate naval"}
PEDIDO: jogos de dinossauros que não sejam da série Jurassic World
{"consulta_positiva": "jogos de dinossauros", "excluir": "série Jurassic World"}
PEDIDO: jogos de ritmo com músicas eletrônicas
{"consulta_positiva": "jogos de ritmo com músicas eletrônicas", "excluir": null}

PEDIDO: {pedido}"""


def mensagens_separar(pedido: str) -> list[dict]:
    return [{"role": "system", "content": SISTEMA},
            {"role": "user", "content": INSTRUCAO_SEPARAR.replace("{pedido}", pedido)}]


def validar_separar(d: dict | None, pedido: str) -> tuple[dict, bool]:
    if not isinstance(d, dict) or not isinstance(d.get("consulta_positiva"), str):
        return {"consulta_positiva": pedido, "excluir": None}, False
    exc = d.get("excluir")
    if exc is not None and not isinstance(exc, str):
        exc = None
    exc = (exc or "").strip() or None
    pos = d["consulta_positiva"].strip() or pedido
    return {"consulta_positiva": pos, "excluir": exc}, True


# --------------------------------------------------------------------- C6
INSTRUCAO_EXTRAIR = """Extraia do pedido as restrições de exclusão e associe-as ao vocabulário de tags da Steam.
Devolva:
- "consulta_positiva": o que a pessoa QUER, reescrito sem a parte excluída;
- "excluir_tags": tags da LISTA abaixo que representam o que a pessoa NÃO quer (use os nomes exatamente como na lista; [] se não houver);
- "excluir_titulos": nomes de jogos ou séries que a pessoa NÃO quer ([] se não houver);
- "excluir_desenvolvedores": empresas que a pessoa NÃO quer ([] se não houver).
Só preencha exclusões que o pedido pede explicitamente.

LISTA DE TAGS (nome original e tradução):
{vocabulario}

Exemplos:
PEDIDO: jogos de aventura com piratas, mas sem combate naval
{"consulta_positiva": "jogos de aventura com piratas", "excluir_tags": ["Naval Combat"], "excluir_titulos": [], "excluir_desenvolvedores": []}
PEDIDO: jogos de dinossauros que não sejam da série Jurassic World
{"consulta_positiva": "jogos de dinossauros", "excluir_tags": [], "excluir_titulos": ["Jurassic World"], "excluir_desenvolvedores": []}
PEDIDO: jogos de ritmo com músicas eletrônicas
{"consulta_positiva": "jogos de ritmo com músicas eletrônicas", "excluir_tags": [], "excluir_titulos": [], "excluir_desenvolvedores": []}

PEDIDO: {pedido}"""


def mensagens_extrair(pedido: str, vocabulario: list[str], traducoes: dict[str, str]) -> list[dict]:
    voc = "; ".join(f"{t} ({traducoes[t]})" if t in traducoes else t for t in vocabulario)
    return [{"role": "system", "content": SISTEMA},
            {"role": "user", "content": INSTRUCAO_EXTRAIR.replace("{vocabulario}", voc).replace("{pedido}", pedido)}]


def validar_extrair(d: dict | None, pedido: str, vocabulario: set[str]) -> tuple[dict, bool]:
    vazio = {"consulta_positiva": pedido, "excluir_tags": [], "excluir_titulos": [], "excluir_desenvolvedores": [],
             "tags_invalidas": []}
    if not isinstance(d, dict) or not isinstance(d.get("consulta_positiva"), str):
        return vazio, False

    def lista(x):
        return [s.strip() for s in x if isinstance(s, str) and s.strip()] if isinstance(x, list) else []

    tags = lista(d.get("excluir_tags"))
    return {"consulta_positiva": d["consulta_positiva"].strip() or pedido,
            "excluir_tags": [t for t in tags if t in vocabulario],
            "excluir_titulos": lista(d.get("excluir_titulos")),
            "excluir_desenvolvedores": lista(d.get("excluir_desenvolvedores")),
            "tags_invalidas": [t for t in tags if t not in vocabulario]}, True
