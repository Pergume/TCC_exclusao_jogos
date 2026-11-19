"""Coleta da base na Steam e no SteamSpy.

Etapas (todas retomáveis; cada jogo concluído é marcado em ``etapas_coleta``):

1. ``candidatos``  — páginas do endpoint ``all`` do SteamSpy (popularidade);
2. ``reviews``     — 1ª página de reviews em PT-BR (GetAppReviews) + contagem total;
3. pré-seleção     — jogos com reviews PT-BR suficientes, em ordem decrescente;
4. ``detalhes``    — appdetails da loja (descrição, gêneros, tipo, descritores);
5. ``tags``        — tags de usuário com votos (SteamSpy appdetails);
6. seleção final   — filtros de tipo, disponibilidade, conteúdo adulto e tags.
"""
from __future__ import annotations

import json
import re
import threading
import time

from ..config import caminho_cache_bruto, caminho_db
from ..db import (conectar, limpar_exclusoes, marcar_etapa, pendentes, registrar_evento,
                  registrar_exclusao)
from .http import ClienteEducado, ErroHTTP

URL_STEAMSPY = "https://steamspy.com/api.php"
URL_APPDETAILS = "https://store.steampowered.com/api/appdetails"
URL_REVIEWS = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"


def _cliente(cfg) -> ClienteEducado:
    c = cfg["coleta"]
    return ClienteEducado(str(caminho_cache_bruto()), c["intervalo"], c["tentativas"], c["timeout"])


def _int(x, padrao=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return padrao


# ---------------------------------------------------------------------------
# 1. Candidatos
# ---------------------------------------------------------------------------
def coletar_candidatos(cfg) -> int:
    con = conectar()
    cli = _cliente(cfg)
    n = 0
    for pagina in range(cfg["coleta"]["steamspy_paginas"]):
        # O SteamSpy às vezes devolve páginas truncadas ou "Too many connections"
        # (ver docs/PROBLEMAS.md, P-001). Página com menos de 1000 jogos é
        # pedida de novo uma vez, sem cache; falha persistente é registrada.
        dados = None
        for tentativa in range(2):
            try:
                dados = cli.get_json("steamspy_all", URL_STEAMSPY, {"request": "all", "page": pagina},
                                     usar_cache=(tentativa == 0), tentativas=4)
            except RuntimeError as e:
                registrar_evento(con, "candidatos", f"página {pagina} falhou", "aviso", erro=str(e)[:200])
            if dados and len(dados) >= 1000:
                break
        if not dados:
            registrar_evento(con, "candidatos", f"página {pagina} vazia ou indisponível", "aviso")
            continue
        if len(dados) < 1000:
            registrar_evento(con, "candidatos", f"página {pagina} incompleta: {len(dados)} jogos", "aviso")
        for pos, item in enumerate(dados.values()):
            con.execute(
                """INSERT INTO candidatos(appid, nome, pagina_steamspy, posicao_steamspy, donos, positivas,
                   negativas, coletado_em, origem) VALUES (?,?,?,?,?,?,?,?, 'steamspy')
                   ON CONFLICT(appid) DO UPDATE SET nome=excluded.nome, pagina_steamspy=excluded.pagina_steamspy,
                   posicao_steamspy=excluded.posicao_steamspy, donos=excluded.donos, positivas=excluded.positivas,
                   negativas=excluded.negativas, coletado_em=excluded.coletado_em,
                   origem=CASE WHEN candidatos.origem='mais_vendidos_br' THEN 'ambos'
                               ELSE COALESCE(candidatos.origem, 'steamspy') END""",
                (_int(item.get("appid")), item.get("name"), pagina, pagina * 1000 + pos, item.get("owners"),
                 _int(item.get("positive")), _int(item.get("negative")), time.time()),
            )
            n += 1
        con.commit()
        registrar_evento(con, "candidatos", f"página {pagina}: {len(dados)} jogos")
    registrar_evento(con, "candidatos", f"total de candidatos: {n}", requisicoes=cli.estatisticas)
    return n


URL_BUSCA_LOJA = "https://store.steampowered.com/search/results/"
_RE_APPID_BUSCA = re.compile(r'data-ds-appid="(\d+)"')


def coletar_mais_vendidos_br(cfg) -> int:
    """Segunda origem de candidatos: mais vendidos da loja brasileira (D-028).

    A lista é dinâmica; o retrato usado fica no cache bruto com a data.
    """
    con = conectar()
    cli = _cliente(cfg)
    maximo = cfg["coleta"].get("mais_vendidos_br_max", 5000)
    con.execute("UPDATE candidatos SET origem='steamspy' WHERE origem IS NULL AND pagina_steamspy IS NOT NULL")
    posicao, novos, inicio = 0, 0, 0
    while inicio < maximo:
        try:
            dados = cli.get_json("loja_busca", URL_BUSCA_LOJA, {
                "filter": "topsellers", "category1": 998, "cc": "br", "l": "brazilian",
                "start": inicio, "count": 100, "json": 1, "infinite": 1})
        except (ErroHTTP, RuntimeError) as e:
            registrar_evento(con, "candidatos", f"mais vendidos BR: falha em start={inicio}", "aviso", erro=str(e)[:200])
            inicio += 100
            continue
        ids = _RE_APPID_BUSCA.findall((dados or {}).get("results_html", ""))
        if not ids:
            break
        for a in ids:
            a = int(a)
            posicao += 1
            existe = con.execute("SELECT origem FROM candidatos WHERE appid=?", (a,)).fetchone()
            if existe:
                con.execute("""UPDATE candidatos SET origem=CASE WHEN origem='steamspy' THEN 'ambos' ELSE origem END,
                               posicao_mais_vendidos_br=COALESCE(posicao_mais_vendidos_br, ?) WHERE appid=?""",
                            (posicao, a))
            else:
                con.execute("""INSERT INTO candidatos(appid, origem, posicao_mais_vendidos_br, coletado_em)
                               VALUES (?,?,?,?)""", (a, "mais_vendidos_br", posicao, time.time()))
                novos += 1
        con.commit()
        total = (dados or {}).get("total_count") or 0
        inicio += 100
        if total and inicio >= total:
            break
    registrar_evento(con, "candidatos", f"mais vendidos BR: {posicao} posições lidas, {novos} candidatos novos")
    return novos


def candidatos_validos(con, cfg) -> list[int]:
    """Aplica o corte mínimo de avaliações do SteamSpy e registra exclusões.

    Candidatos vindos só dos mais vendidos BR não têm contagem do SteamSpy e
    seguem direto para a etapa de reviews (o corte de reviews PT-BR vale para todos).
    """
    limpar_exclusoes(con, "candidatos")
    minimo = cfg["coleta"]["min_avaliacoes_steamspy"]
    validos = []
    for r in con.execute("""SELECT appid, nome, positivas, negativas, origem FROM candidatos
                            ORDER BY COALESCE(posicao_steamspy, 1e9), posicao_mais_vendidos_br"""):
        total = (r["positivas"] or 0) + (r["negativas"] or 0)
        if r["origem"] == "mais_vendidos_br":
            validos.append(r["appid"])
            continue
        if total < minimo and r["origem"] != "ambos":
            registrar_exclusao(con, "jogo", r["appid"], "candidatos", "poucas_avaliacoes_steamspy", commit=False,
                               nome=r["nome"], total=total, minimo=minimo)
        else:
            validos.append(r["appid"])
    con.commit()
    return validos


# ---------------------------------------------------------------------------
# 2. Reviews em PT-BR
# ---------------------------------------------------------------------------
def _params_reviews(appid: int, n: int) -> dict:
    # filter=0 (mais úteis), day_range=0 (sem limite de data), compras na Steam (padrão).
    return {"input_json": json.dumps({
        "appid": appid, "languages": ["brazilian"], "num_per_page": n, "cursor": "*",
        "filter": 0, "day_range": 0, "review_type": 0,
    })}


def coletar_reviews(cfg, appids: list[int] | None = None) -> None:
    con = conectar()
    cli = _cliente(cfg)
    if appids is None:
        appids = candidatos_validos(con, cfg)
    fila = pendentes(con, "reviews", appids)
    registrar_evento(con, "reviews", f"{len(fila)} jogos pendentes de {len(appids)}")
    n_por = cfg["coleta"]["reviews_por_jogo"]
    for i, appid in enumerate(fila, 1):
        try:
            dados = cli.get_json("reviews", URL_REVIEWS, _params_reviews(appid, n_por), chave=f"{appid}:{n_por}")
            resp = (dados or {}).get("response") or {}
            qs = resp.get("query_summary") or {}
            con.execute(
                """INSERT OR REPLACE INTO reviews_resumo(appid, total_reviews, total_positivas, total_negativas,
                   review_score_desc, coletado_em) VALUES (?,?,?,?,?,?)""",
                (appid, _int(qs.get("total_reviews")), _int(qs.get("total_positive")),
                 _int(qs.get("total_negative")), qs.get("review_score_desc"), time.time()),
            )
            for ordem, rv in enumerate(resp.get("reviews") or [], 1):
                autor = rv.get("author") or {}
                con.execute(
                    """INSERT OR REPLACE INTO reviews(recommendationid, appid, ordem, texto, idioma, recomendado,
                       votos_util, votos_engracado, weighted_vote_score, criado_em, horas_no_review,
                       acesso_antecipado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(rv.get("recommendationid")), appid, ordem, rv.get("review"), rv.get("language"),
                     int(bool(rv.get("voted_up"))), _int(rv.get("votes_up")), _int(rv.get("votes_funny")),
                     float(rv.get("weighted_vote_score") or 0), _int(rv.get("timestamp_created")),
                     round(_int(autor.get("playtime_at_review")) / 60, 1),
                     int(bool(rv.get("written_during_early_access")))),
                )
            marcar_etapa(con, "reviews", appid, "ok" if resp else "vazio")
        except (ErroHTTP, RuntimeError) as e:
            marcar_etapa(con, "reviews", appid, "erro", str(e)[:300])
            registrar_evento(con, "reviews", f"erro no appid {appid}", "aviso", erro=str(e)[:300])
        con.commit()  # commit por jogo: evita travar o banco com várias linhas de coleta (P-006)
        if i % 50 == 0:
            print(f"reviews: {i}/{len(fila)} {cli.estatisticas}", flush=True)
    con.commit()
    registrar_evento(con, "reviews", "concluído", requisicoes=cli.estatisticas)


# ---------------------------------------------------------------------------
# 3. Pré-seleção por volume de reviews em PT-BR
# ---------------------------------------------------------------------------
def pre_selecionar(con, cfg, folga: float = 1.1) -> list[int]:
    """Jogos com reviews PT-BR suficientes, em ordem decrescente (com folga
    sobre ``max_jogos`` para compensar os filtros das etapas seguintes)."""
    limpar_exclusoes(con, "pre_selecao")
    minimo = cfg["coleta"]["min_reviews_ptbr"]
    limite = int(cfg["coleta"]["max_jogos"] * folga)
    linhas = con.execute(
        """SELECT c.appid, c.nome, r.total_reviews FROM candidatos c
           JOIN reviews_resumo r USING(appid) ORDER BY r.total_reviews DESC, c.appid"""
    ).fetchall()
    escolhidos = []
    for r in linhas:
        if (r["total_reviews"] or 0) < minimo:
            registrar_exclusao(con, "jogo", r["appid"], "pre_selecao", "poucas_reviews_ptbr", commit=False,
                               nome=r["nome"], total_reviews_ptbr=r["total_reviews"], minimo=minimo)
        elif len(escolhidos) >= limite:
            registrar_exclusao(con, "jogo", r["appid"], "pre_selecao", "fora_do_corte_pre_selecao", commit=False,
                               nome=r["nome"], total_reviews_ptbr=r["total_reviews"])
        else:
            escolhidos.append(r["appid"])
    con.commit()
    registrar_evento(con, "pre_selecao", f"{len(escolhidos)} jogos pré-selecionados (mínimo {minimo} reviews PT-BR)")
    return escolhidos


# ---------------------------------------------------------------------------
# 4. Detalhes da loja
# ---------------------------------------------------------------------------
def coletar_detalhes(cfg, appids: list[int]) -> None:
    con = conectar()
    cli = _cliente(cfg)
    fila = pendentes(con, "detalhes", appids)
    registrar_evento(con, "detalhes", f"{len(fila)} jogos pendentes")
    for i, appid in enumerate(fila, 1):
        try:
            dados = cli.get_json("loja_appdetails", URL_APPDETAILS,
                                 {"appids": appid, "l": "brazilian", "cc": "br"}, chave=f"{appid}:brazilian:br")
            item = (dados or {}).get(str(appid)) or {}
            if not item.get("success"):
                marcar_etapa(con, "detalhes", appid, "vazio", "success=false (indisponível na loja BR)")
                continue
            d = item.get("data") or {}
            con.execute(
                """INSERT OR REPLACE INTO jogos(appid, nome, tipo, descricao_curta, sobre, generos, categorias,
                   descritores_conteudo, idiomas_suportados, data_lancamento, desenvolvedores, publicadoras,
                   gratuito, coletado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (appid, d.get("name"), d.get("type"), d.get("short_description"),
                 d.get("about_the_game") or d.get("detailed_description"),
                 json.dumps([g.get("description") for g in d.get("genres") or []], ensure_ascii=False),
                 json.dumps([c.get("description") for c in d.get("categories") or []], ensure_ascii=False),
                 json.dumps((d.get("content_descriptors") or {}).get("ids") or []),
                 d.get("supported_languages"), (d.get("release_date") or {}).get("date"),
                 json.dumps(d.get("developers") or [], ensure_ascii=False),
                 json.dumps(d.get("publishers") or [], ensure_ascii=False),
                 int(bool(d.get("is_free"))), time.time()),
            )
            marcar_etapa(con, "detalhes", appid, "ok")
        except (ErroHTTP, RuntimeError) as e:
            marcar_etapa(con, "detalhes", appid, "erro", str(e)[:300])
            registrar_evento(con, "detalhes", f"erro no appid {appid}", "aviso", erro=str(e)[:300])
        con.commit()  # commit por jogo: evita travar o banco com várias linhas de coleta (P-006)
        if i % 50 == 0:
            print(f"detalhes: {i}/{len(fila)} {cli.estatisticas}", flush=True)
    con.commit()
    registrar_evento(con, "detalhes", "concluído", requisicoes=cli.estatisticas)


# ---------------------------------------------------------------------------
# 5. Tags de usuário (SteamSpy)
# ---------------------------------------------------------------------------
URL_PAGINA_LOJA = "https://store.steampowered.com/app/{appid}/"
# Cookies de verificação de idade: sem eles, jogos com classificação adulta
# mostram a página de confirmação em vez da página do jogo.
COOKIES_IDADE = "birthtime=470703601; mature_content=1; wants_mature_content=1; lastagecheckage=1-0-1985"
_RE_TAGS = re.compile(r"InitAppTagModal\(\s*\d+,\s*(\[.*?\])\s*,", re.S)


def tags_da_pagina_loja(cli: ClienteEducado, appid: int) -> dict[str, int]:
    """Tags de usuário embutidas na página da loja (mesmos dados do SteamSpy)."""
    html = cli.get_texto("loja_pagina", URL_PAGINA_LOJA.format(appid=appid), {"l": "english"},
                         chave=f"{appid}:english", cookies=COOKIES_IDADE)
    m = _RE_TAGS.search(html or "")
    if not m:
        return {}
    return {t["name"]: _int(t.get("count")) for t in json.loads(m.group(1)) if t.get("name")}


def coletar_tags(cfg, appids: list[int]) -> None:
    """Tags de usuário: SteamSpy primeiro; se falhar, página da loja (P-001)."""
    con = conectar()
    cli = _cliente(cfg)
    fila = pendentes(con, "tags", appids)
    registrar_evento(con, "tags", f"{len(fila)} jogos pendentes")
    for i, appid in enumerate(fila, 1):
        try:
            fonte = "steamspy"
            try:
                dados = cli.get_json("steamspy", URL_STEAMSPY, {"request": "appdetails", "appid": appid},
                                     tentativas=3)
                tags = (dados or {}).get("tags") or {}
                if isinstance(tags, list):  # o SteamSpy devolve [] quando não há tags
                    tags = {}
            except RuntimeError:
                tags = {}
            if not tags:
                fonte = "loja"
                tags = tags_da_pagina_loja(cli, appid)
            con.execute("DELETE FROM tags WHERE appid=?", (appid,))
            # Algumas tags vêm com espaço sobrando ("Dystopian ", "Parody "): P-009.
            tags = {t.strip(): v for t, v in tags.items() if t and t.strip()}
            for pos, (tag, votos) in enumerate(sorted(tags.items(), key=lambda kv: -_int(kv[1])), 1):
                con.execute("INSERT INTO tags(appid, tag, votos, posicao) VALUES (?,?,?,?)",
                            (appid, tag, _int(votos), pos))
            marcar_etapa(con, "tags", appid, "ok" if tags else "vazio", f"fonte={fonte}")
        except (ErroHTTP, RuntimeError) as e:
            marcar_etapa(con, "tags", appid, "erro", str(e)[:300])
            registrar_evento(con, "tags", f"erro no appid {appid}", "aviso", erro=str(e)[:300])
        con.commit()  # commit por jogo: evita travar o banco com várias linhas de coleta (P-006)
        if i % 50 == 0:
            print(f"tags: {i}/{len(fila)} {cli.estatisticas}", flush=True)
    con.commit()
    registrar_evento(con, "tags", "concluído", requisicoes=cli.estatisticas)


# ---------------------------------------------------------------------------
# 6. Seleção final
# ---------------------------------------------------------------------------
def selecionar(cfg) -> int:
    con = conectar()
    limpar_exclusoes(con, "selecao")
    con.execute("DELETE FROM selecionados")
    proibidos = set(cfg["coleta"]["excluir_descritores_conteudo"])
    maximo = cfg["coleta"]["max_jogos"]
    pre = pre_selecionar(con, cfg)
    status = {(r["etapa"], r["appid"]): r["status"] for r in con.execute("SELECT etapa, appid, status FROM etapas_coleta")}
    jogos = {r["appid"]: r for r in con.execute("SELECT * FROM jogos")}
    n_tags = dict(con.execute("SELECT appid, COUNT(*) FROM tags GROUP BY appid").fetchall())
    nomes = dict(con.execute("SELECT appid, nome FROM candidatos").fetchall())
    escolhidos = []
    for appid in pre:
        motivo, extra = None, {}
        j = jogos.get(appid)
        if status.get(("detalhes", appid)) is None or status.get(("tags", appid)) is None:
            motivo = "coleta_incompleta"
        elif j is None:
            motivo = "indisponivel_na_loja_br"
        elif j["tipo"] != "game":
            motivo, extra = "nao_e_jogo", {"tipo": j["tipo"]}
        elif proibidos & set(json.loads(j["descritores_conteudo"] or "[]")):
            motivo, extra = "conteudo_adulto_explicito", {"descritores": j["descritores_conteudo"]}
        elif not n_tags.get(appid):
            motivo = "sem_tags"
        elif len(escolhidos) >= maximo:
            motivo = "fora_do_corte_max_jogos"
        if motivo:
            registrar_exclusao(con, "jogo", appid, "selecao", motivo, commit=False, nome=nomes.get(appid), **extra)
            continue
        escolhidos.append(appid)
        con.execute("INSERT INTO selecionados(appid, posicao, selecionado_em) VALUES (?,?,?)",
                    (appid, len(escolhidos), time.time()))
    con.commit()
    registrar_evento(con, "selecao", f"base final: {len(escolhidos)} jogos")
    return len(escolhidos)


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------
def coletar_tudo(cfg, pular_candidatos: bool = False) -> None:
    """Executa todas as etapas. Detalhes e tags rodam em paralelo (serviços
    diferentes, cada um respeitando o próprio intervalo)."""
    con = conectar()
    if not pular_candidatos or not con.execute("SELECT COUNT(*) FROM candidatos").fetchone()[0]:
        coletar_candidatos(cfg)
        if cfg["coleta"].get("mais_vendidos_br"):
            coletar_mais_vendidos_br(cfg)
    validos = candidatos_validos(con, cfg)
    # Reviews em duas linhas paralelas (cada uma com seu intervalo mínimo);
    # respostas 429 disparam espera exponencial.
    metades = [validos[0::2], validos[1::2]]
    ts = [threading.Thread(target=coletar_reviews, args=(cfg, m)) for m in metades]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    pre = pre_selecionar(con, cfg)
    t1 = threading.Thread(target=coletar_detalhes, args=(cfg, pre))
    t2 = threading.Thread(target=coletar_tags, args=(cfg, pre))
    t1.start(); t2.start(); t1.join(); t2.join()
    selecionar(cfg)
