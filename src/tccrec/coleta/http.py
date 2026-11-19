"""Cliente HTTP educado: intervalo mínimo por serviço, novas tentativas com
espera exponencial em 429/5xx e cache bruto das respostas (reprodutibilidade).
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

from ..db import conectar_bruto

AGENTE = "tcc-exclusao-jogos/0.1 (pesquisa academica; UNIR)"


class ErroHTTP(Exception):
    def __init__(self, status: int, url: str, corpo: str = ""):
        super().__init__(f"HTTP {status} em {url}: {corpo[:200]}")
        self.status = status
        self.url = url


class ClienteEducado:
    def __init__(self, caminho_cache: str, intervalos: dict[str, float], tentativas: int = 6, timeout: int = 30):
        self.cache = conectar_bruto(caminho_cache)
        self.intervalos = intervalos
        self.tentativas = tentativas
        self.timeout = timeout
        self._ultimo: dict[str, float] = {}
        self.estatisticas = {"requisicoes": 0, "cache": 0, "429": 0, "erros": 0}

    # -- cache ---------------------------------------------------------------
    def _ler_cache(self, fonte: str, chave: str) -> Any | None:
        row = self.cache.execute("SELECT corpo FROM respostas WHERE fonte=? AND chave=? AND status=200",
                                 (fonte, chave)).fetchone()
        if row:
            self.estatisticas["cache"] += 1
            return json.loads(zlib.decompress(row[0]).decode("utf-8"))
        return None

    def _gravar_cache(self, fonte: str, chave: str, url: str, status: int, corpo: str) -> None:
        self.cache.execute(
            "INSERT OR REPLACE INTO respostas(fonte, chave, url, status, corpo, coletado_em) VALUES (?,?,?,?,?,?)",
            (fonte, chave, url, status, zlib.compress(corpo.encode("utf-8")), time.time()),
        )
        self.cache.commit()

    # -- requisição ----------------------------------------------------------
    def _esperar(self, servico: str) -> None:
        minimo = self.intervalos.get(servico, 1.0)
        decorrido = time.time() - self._ultimo.get(servico, 0.0)
        if decorrido < minimo:
            time.sleep(minimo - decorrido)
        self._ultimo[servico] = time.time()

    def get_json(self, servico: str, url: str, params: dict | None = None, chave: str | None = None,
                 usar_cache: bool = True, tentativas: int | None = None) -> Any:
        return self._get(servico, url, params, chave, usar_cache, tentativas, json_=True)

    def get_texto(self, servico: str, url: str, params: dict | None = None, chave: str | None = None,
                  usar_cache: bool = True, tentativas: int | None = None, cookies: str | None = None) -> str:
        return self._get(servico, url, params, chave, usar_cache, tentativas, json_=False, cookies=cookies)

    def _get(self, servico, url, params, chave, usar_cache, tentativas, json_: bool, cookies: str | None = None):
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        chave = chave or url
        if usar_cache:
            em_cache = self._ler_cache(servico, chave)
            if em_cache is not None:
                return em_cache if json_ else em_cache["texto"]
        espera = 5.0
        ultimo_erro: Exception | None = None
        for tentativa in range(1, (tentativas or self.tentativas) + 1):
            self._esperar(servico)
            try:
                cab = {"User-Agent": AGENTE, "Accept": "application/json" if json_ else "text/html"}
                if cookies:
                    cab["Cookie"] = cookies
                req = urllib.request.Request(url, headers=cab)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    corpo = resp.read().decode("utf-8", errors="replace")
                    self.estatisticas["requisicoes"] += 1
                    if json_:
                        dados = json.loads(corpo) if corpo.strip() else None
                        self._gravar_cache(servico, chave, url, 200, corpo)
                        return dados
                    self._gravar_cache(servico, chave, url, 200, json.dumps({"texto": corpo}))
                    return corpo
            except urllib.error.HTTPError as e:
                self.estatisticas["requisicoes"] += 1
                if e.code == 429 or e.code >= 500:
                    self.estatisticas["429" if e.code == 429 else "erros"] += 1
                    ultimo_erro = ErroHTTP(e.code, url)
                    time.sleep(espera + random.random())
                    espera = min(espera * 2, 300)
                    continue
                raise ErroHTTP(e.code, url, e.read().decode("utf-8", errors="replace")) from e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as e:
                self.estatisticas["erros"] += 1
                ultimo_erro = e
                time.sleep(espera + random.random())
                espera = min(espera * 2, 300)
        raise RuntimeError(f"Falha após {self.tentativas} tentativas: {url} ({ultimo_erro})")
