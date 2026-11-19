"""Leitura do conjunto de teste, resolução de nomes e cálculo do gabarito.

O gabarito é derivado automaticamente das tags de usuário da Steam:

* ``positivo``: o jogo satisfaz todos os grupos da parte positiva do pedido;
* ``viola``: o jogo tem alguma tag do atributo excluído, um título excluído
  (nome exato ou padrão) ou uma desenvolvedora/publicadora excluída;
* ``relevante``: na versão "sem", igual a ``positivo``; na versão "com",
  ``positivo`` e não ``viola``.

O jogo de referência citado no pedido nunca conta como relevante e é removido
dos rankings na avaliação.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import RAIZ
from ..base.texto import sem_acentos

TIPOS = ("estilo_visual", "dificuldade", "modo_de_jogo", "tema", "titulo_referencia")


def normalizar_nome(s: str | None) -> str:
    s = sem_acentos((s or "").lower())
    s = re.sub(r"[™®©]", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


@dataclass
class Par:
    id: str
    tipo: str
    com: str
    sem: str
    positivo: list[list[str]]
    exclusao: dict
    referencia: dict | None = None
    # resolvidos contra a base
    ref_appid: int | None = None
    tags_excluidas: list[str] = field(default_factory=list)
    appids_excluidos: set[int] = field(default_factory=set)
    problemas: list[str] = field(default_factory=list)


def carregar_conjunto(cfg) -> tuple[dict, list[Par]]:
    caminho = Path(cfg["teste"]["arquivo"])
    if not caminho.is_absolute():
        caminho = RAIZ / caminho
    with open(caminho, encoding="utf-8") as f:
        dados = yaml.safe_load(f)
    atributos = dados["atributos"]
    pares = []
    ids = set()
    for p in dados["pares"]:
        if p["id"] in ids:
            raise ValueError(f"id de par repetido: {p['id']}")
        ids.add(p["id"])
        if p["tipo"] not in TIPOS:
            raise ValueError(f"{p['id']}: tipo inválido {p['tipo']}")
        pares.append(Par(id=p["id"], tipo=p["tipo"], com=p["com"], sem=p["sem"], positivo=p["positivo"],
                         exclusao=p.get("exclusao") or {}, referencia=p.get("referencia")))
    textos = [t for p in pares for t in (p.com, p.sem)]
    repetidos = {t for t in textos if textos.count(t) > 1}
    if repetidos:
        raise ValueError(f"pedidos repetidos no conjunto: {sorted(repetidos)}")
    return atributos, pares


class Base:
    """Visão da base selecionada usada pelo gabarito."""

    def __init__(self, con, top_n: int):
        self.appids = [r[0] for r in con.execute("SELECT appid FROM selecionados ORDER BY posicao")]
        sel = set(self.appids)
        self.nome = {}
        self.devs: dict[int, str] = {}
        for r in con.execute("SELECT appid, nome, desenvolvedores, publicadoras FROM jogos"):
            if r["appid"] in sel:
                self.nome[r["appid"]] = r["nome"]
                self.devs[r["appid"]] = " | ".join(json.loads(r["desenvolvedores"] or "[]") +
                                                   json.loads(r["publicadoras"] or "[]"))
        self.tags: dict[int, set[str]] = {a: set() for a in self.appids}
        for r in con.execute("SELECT appid, tag FROM tags WHERE posicao <= ?", (top_n,)):
            if r["appid"] in sel:
                self.tags[r["appid"]].add(r["tag"])
        self.vocabulario = {t for ts in self.tags.values() for t in ts}
        self._por_nome = {normalizar_nome(n): a for a, n in self.nome.items()}

    def resolver(self, nome: str | None, appid: int | None) -> int | None:
        if appid in self.nome and normalizar_nome(self.nome[appid]).startswith(normalizar_nome(nome)[:12]):
            return appid
        if nome and normalizar_nome(nome) in self._por_nome:
            return self._por_nome[normalizar_nome(nome)]
        if appid in self.nome:  # appid presente mas nome diferente: aceita e avisa
            return appid
        return None

    def por_padrao(self, padrao: str) -> set[int]:
        alvo = normalizar_nome(padrao)
        return {a for a, n in self.nome.items() if alvo in normalizar_nome(n)}

    def por_desenvolvedor(self, nome: str) -> set[int]:
        alvo = normalizar_nome(nome)
        return {a for a, d in self.devs.items() if alvo in normalizar_nome(d)}


def resolver_par(par: Par, atributos: dict, base: Base) -> None:
    """Preenche referência, tags e títulos excluídos e anota problemas."""
    if par.referencia:
        par.ref_appid = base.resolver(par.referencia.get("nome"), par.referencia.get("appid"))
        if par.ref_appid is None:
            par.problemas.append(f"referência fora da base: {par.referencia}")
        elif normalizar_nome(base.nome[par.ref_appid]) != normalizar_nome(par.referencia.get("nome")):
            par.problemas.append(f"aviso: referência resolvida para '{base.nome[par.ref_appid]}'")
    # parte positiva
    for grupo in par.positivo:
        faltando = [t for t in grupo if t not in base.vocabulario]
        if faltando:
            par.problemas.append(f"aviso: tags positivas ausentes da base: {faltando}")
        if not any(t in base.vocabulario for t in grupo):
            par.problemas.append(f"grupo positivo sem nenhuma tag existente: {grupo}")
    # exclusão
    ex = par.exclusao
    if "atributo" in ex:
        attr = atributos.get(ex["atributo"])
        if attr is None:
            par.problemas.append(f"atributo desconhecido: {ex['atributo']}")
        else:
            par.tags_excluidas = [t for t in attr["tags"] if t in base.vocabulario]
            faltando = [t for t in attr["tags"] if t not in base.vocabulario]
            if faltando:
                par.problemas.append(f"aviso: tags excluídas ausentes da base: {faltando}")
            if not par.tags_excluidas:
                par.problemas.append("nenhuma tag do atributo excluído existe na base")
    for nome in ex.get("titulos", []):
        a = base.resolver(nome, None)
        if a is None:
            par.problemas.append(f"aviso: título excluído fora da base: {nome}")
        else:
            par.appids_excluidos.add(a)
    for padrao in ex.get("padrao_titulo", []):
        par.appids_excluidos |= base.por_padrao(padrao)
    for dev in ex.get("desenvolvedor", []):
        par.appids_excluidos |= base.por_desenvolvedor(dev)
    if not ("atributo" in ex or ex.get("titulos") or ex.get("padrao_titulo") or ex.get("desenvolvedor")):
        par.problemas.append("par sem exclusão definida")


def julgar(par: Par, base: Base, appid: int) -> tuple[bool, bool]:
    """Devolve (positivo, viola) para um jogo."""
    tags = base.tags.get(appid, set())
    positivo = appid != par.ref_appid and all(any(t in tags for t in g) for g in par.positivo)
    viola = bool(tags & set(par.tags_excluidas)) or appid in par.appids_excluidos
    return positivo, viola


def estatisticas_par(par: Par, base: Base) -> dict:
    pos = viol = pos_viol = 0
    for a in base.appids:
        p, v = julgar(par, base, a)
        pos += p
        viol += v
        pos_viol += p and v
    return {"positivos": pos, "violadores": viol, "positivos_violadores": pos_viol,
            "relevantes_com": pos - pos_viol,
            "proporcao_conflito": round(pos_viol / pos, 3) if pos else 0.0}


def motivo_descartar(par: Par, est: dict, cfg) -> str | None:
    t = cfg["teste"]
    if any(not pr.startswith("aviso") for pr in par.problemas):
        return "erro_de_resolucao"
    if est["positivos"] < t["min_positivos"]:
        return "poucos_positivos"
    if est["relevantes_com"] < t["min_relevantes_com"]:
        return "poucos_relevantes_na_versao_com"
    if "atributo" in par.exclusao:
        if est["proporcao_conflito"] < t["min_proporcao_conflito"]:
            return "exclusao_nao_morde"
    elif est["positivos_violadores"] < t["min_violadores_titulo"]:
        return "titulo_excluido_nao_aparece_entre_positivos"
    return None
