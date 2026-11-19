#!/usr/bin/env bash
# Empacota a pasta de dados (TCC_DATA_DIR) para guardar no Google Drive ou
# entregar junto da monografia. Não inclui os índices (são regerados com
# `python -m tccrec indexar`).
#
# Uso: TCC_DATA_DIR=/caminho/dados bash scripts/empacotar_dados.sh [saida.zip]
set -euo pipefail
DADOS="${TCC_DATA_DIR:-./dados}"
SAIDA="${1:-tcc-dados-$(date +%Y%m%d).zip}"
case "$SAIDA" in /*) ;; *) SAIDA="$PWD/$SAIDA" ;; esac
cd "$DADOS"
# Garante que o SQLite gravou tudo do WAL no arquivo principal antes de copiar.
python3 - <<'EOF'
import sqlite3
for nome in ("base.sqlite", "respostas_brutas.sqlite"):
    try:
        con = sqlite3.connect(nome)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
    except sqlite3.Error as e:
        print("aviso:", nome, e)
EOF
zip -r -q "$SAIDA" . -x "indices/*" "*.sqlite-wal" "*.sqlite-shm"
echo "Gerado: $SAIDA"
