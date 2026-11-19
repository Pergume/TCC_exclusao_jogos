"""Interface de linha de comando: ``python -m tccrec <comando>``.

Ordem típica (ver README):
    coletar → preparar-base → validar-teste → gabarito → indexar → rodar → avaliar
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import carregar_config


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tccrec", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="caminho do YAML de configuração (padrão: config/config.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("coletar", help="coleta a base na Steam/SteamSpy (retomável)")
    c.add_argument("--etapa", choices=["tudo", "candidatos", "mais-vendidos-br", "reviews", "detalhes", "tags", "selecionar"], default="tudo")

    sub.add_parser("preparar-base", help="limpa textos e gera os documentos das duas fontes")
    sub.add_parser("validar-teste", help="valida o conjunto de teste contra a base e registra exclusões de pares")
    sub.add_parser("gabarito", help="gera consultas e qrels (relevância e violação) a partir das tags")

    i = sub.add_parser("indexar", help="constrói os índices BM25 e densos (ChromaDB)")
    i.add_argument("--fonte", choices=["descricao_tags", "reviews", "todas"], default="todas")
    i.add_argument("--so-bm25", action="store_true")

    r = sub.add_parser("rodar", help="executa configurações de busca e grava os rankings")
    r.add_argument("--configs", default="C1,C2,C3,C4,C5,C6", help="lista separada por vírgula")
    r.add_argument("--fonte", choices=["descricao_tags", "reviews", "todas"], default="todas")
    r.add_argument("--limite-consultas", type=int, default=None, help="só as N primeiras consultas (teste rápido)")

    sub.add_parser("avaliar", help="calcula métricas, testes estatísticos e exporta tabelas")
    a = sub.add_parser("amostra-manual", help="exporta amostra para conferência manual do gabarito")
    a.add_argument("--exec", dest="exec_id", default=None, help="execução de onde sortear (padrão: C3)")
    k = sub.add_parser("concordancia", help="compara a conferência manual com o gabarito automático")
    k.add_argument("arquivo", help="CSV preenchido da amostra manual")
    sub.add_parser("relatorio", help="gera docs/RELATORIO_BASE.md e docs/EXCLUSOES.md a partir do banco")
    sub.add_parser("exportar", help="exporta tabelas da base para CSV (dados/export)")

    args = p.parse_args(argv)
    cfg = carregar_config(args.config)

    if args.cmd == "coletar":
        from .coleta import steam
        from .db import conectar
        if args.etapa == "tudo":
            steam.coletar_tudo(cfg, pular_candidatos=True)
        elif args.etapa == "candidatos":
            steam.coletar_candidatos(cfg)
            if cfg["coleta"].get("mais_vendidos_br"):
                steam.coletar_mais_vendidos_br(cfg)
        elif args.etapa == "mais-vendidos-br":
            steam.coletar_mais_vendidos_br(cfg)
        elif args.etapa == "reviews":
            steam.coletar_reviews(cfg)
        elif args.etapa in ("detalhes", "tags"):
            pre = steam.pre_selecionar(conectar(), cfg)
            (steam.coletar_detalhes if args.etapa == "detalhes" else steam.coletar_tags)(cfg, pre)
        elif args.etapa == "selecionar":
            steam.selecionar(cfg)
    elif args.cmd == "preparar-base":
        from .base.preparar import preparar_base
        preparar_base(cfg)
    elif args.cmd == "validar-teste":
        from .teste.validar import validar_conjunto
        validar_conjunto(cfg)
    elif args.cmd == "gabarito":
        from .teste.gabarito import gerar_gabarito
        gerar_gabarito(cfg)
    elif args.cmd == "indexar":
        from .busca.indices import indexar
        indexar(cfg, args.fonte, so_bm25=args.so_bm25)
    elif args.cmd == "rodar":
        from .busca.configuracoes import rodar
        rodar(cfg, args.configs.split(","), args.fonte, args.limite_consultas)
    elif args.cmd == "avaliar":
        from .avaliacao.avaliar import avaliar
        avaliar(cfg)
    elif args.cmd == "amostra-manual":
        from .avaliacao.manual import exportar_amostra
        exportar_amostra(cfg, args.exec_id)
    elif args.cmd == "concordancia":
        from .avaliacao.manual import concordancia
        print(json.dumps(concordancia(cfg, args.arquivo), ensure_ascii=False, indent=2))
    elif args.cmd == "relatorio":
        from .relatorios import gerar_relatorios
        gerar_relatorios(cfg)
    elif args.cmd == "exportar":
        from .relatorios import exportar_csv
        exportar_csv(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
