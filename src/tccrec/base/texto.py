"""Utilidades de texto: limpeza de HTML, normalização e tokenização."""
from __future__ import annotations

import html
import re
import unicodedata

_TAG_HTML = re.compile(r"<[^>]+>")
_BBCODE = re.compile(r"\[/?[a-zA-Z0-9*]+(=[^\]]*)?\]")
_ESPACOS = re.compile(r"\s+")
_PALAVRA = re.compile(r"[a-z0-9]+")

# Palavras funcionais do português (detecção de idioma e stopwords do BM25).
FUNCIONAIS_PT = set("""
a o as os um uma uns umas de do da dos das no na nos nas em por para pra pro com sem e ou mas
que se nao é eh ja muito mais menos bem mal tem ter tá ta esta está estao sao são foi ser
isso esse essa este esta aqui ali lá la voce você vc eu ele ela eles elas nos nós meu minha
seu sua jogo jogar porque pq quando como onde tudo nada nem so só ainda também tambem até ate
""".split())

STOPWORDS_EN = set("""
a an the of to in on for with and or but is are was were be been it its this that these those
as at by from you your i we they he she not no so if then than very
""".split())


def html_para_texto(s: str | None) -> str:
    if not s:
        return ""
    s = s.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ").replace("</li>", ". ")
    s = _TAG_HTML.sub(" ", s)
    s = html.unescape(s)
    return _ESPACOS.sub(" ", s).strip()


def limpar_review(s: str | None) -> str:
    if not s:
        return ""
    s = _BBCODE.sub(" ", s)
    return _ESPACOS.sub(" ", s).strip()


def sem_acentos(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


_PALAVRA_ACENTOS = re.compile(r"[a-zà-öø-ÿ0-9]+")
_STOP = None
_RADICALIZADOR = None


def _radical(tok: str) -> str:
    """Radicalizador Snowball para o português (D-030); sem a biblioteca, devolve o token."""
    global _RADICALIZADOR
    if _RADICALIZADOR is None:
        try:
            import snowballstemmer
            _RADICALIZADOR = snowballstemmer.stemmer("portuguese")
        except ImportError:  # pragma: no cover
            _RADICALIZADOR = False
    return _RADICALIZADOR.stemWord(tok) if _RADICALIZADOR else tok


def tokenizar(s: str, remover_stopwords: bool = True, radicalizar: bool = True) -> list[str]:
    """Tokenização para o BM25: minúsculas, palavras funcionais removidas,
    radical Snowball do português e, por fim, sem acentos.

    "sem" e "não" são mantidos: a negação é justamente o objeto de estudo e
    removê-la mudaria o comportamento do BM25 (D-021).
    """
    global _STOP
    if _STOP is None:
        _STOP = {sem_acentos(w) for w in FUNCIONAIS_PT if w not in {"jogo", "jogar", "sem", "nao", "não"}} | STOPWORDS_EN
    out = []
    for tok in _PALAVRA_ACENTOS.findall(s.lower()):
        if remover_stopwords and sem_acentos(tok) in _STOP:
            continue
        if sem_acentos(tok) in {"sem", "nao"}:  # marcadores de negação ficam intactos
            out.append(sem_acentos(tok))
        else:
            out.append(sem_acentos(_radical(tok) if radicalizar else tok))
    return out


# Palavras que existem também em inglês ("a", "as", "no", "so"...) não contam
# como evidência de português.
_AMBIGUAS = STOPWORDS_EN | {"so", "do", "pro", "me", "nos", "ate", "ter", "ser", "bem", "mal"}


def proporcao_portugues(s: str) -> float:
    toks = _PALAVRA.findall(sem_acentos(s.lower()))
    if not toks:
        return 0.0
    funcionais = {sem_acentos(w) for w in FUNCIONAIS_PT} - _AMBIGUAS
    return sum(1 for t in toks if t in funcionais) / len(toks)


def proporcao_letras(s: str) -> float:
    if not s:
        return 0.0
    return sum(1 for c in s if c.isalpha()) / len(s)
