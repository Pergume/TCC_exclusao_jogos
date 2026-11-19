# "Como X, mas sem Y": restrições de exclusão em recomendação de jogos em português

Código do Trabalho de Conclusão de Curso de Pedro Augusto Araújo de Medeiros (Bacharelado em Ciência da Computação, Universidade Federal de Rondônia — UNIR, 2026).

O trabalho mede se técnicas de recuperação de informação respeitam pedidos como **"jogos como Hollow Knight, mas sem pixel art"**, escritos em português, usando dados reais da Steam. Seis configurações de busca são comparadas sobre duas fontes de texto, e as tags de usuário da Steam servem de gabarito automático: um jogo recomendado para "sem pixel art" que tenha a tag *Pixel Graphics* viola o pedido.

> **Pergunta de pesquisa:** em que medida técnicas de recuperação de informação lexical, densa, híbrida, com reranqueamento e com tratamento explícito da exclusão respeitam as restrições de exclusão presentes em pedidos de recomendação de jogos digitais escritos em português brasileiro?

| Pergunta | O que mede | Comando que produz a evidência |
| --- | --- | --- |
| P1 — O "sem" é ignorado? | Violação com exclusão × sem exclusão, nas técnicas de base (C1–C3) | `avaliar` → `teste_P1.csv` |
| P2 — Tratar a exclusão resolve? | Violação e nDCG de C4–C6 contra C3 | `avaliar` → `teste_P2.csv` |
| P3 — O texto indexado importa? | Violação na fonte de reviews × descrições e tags | `avaliar` → `teste_P3.csv` |

## Estado atual (17/11/2026)

| Etapa | Situação |
| --- | --- |
| Base de dados | **Coletada.** 3000 jogos, 263.650 reviews em PT-BR baixadas, 139.494 indexadas; funil completo em |
| Conjunto de teste | **Validado:** 63 de 64 pares mantidos (126 consultas), gabarito com 78.348 linhas. Falta a revisão da autora |
| Configurações C1–C6 | **Implementadas e testadas** (testes automáticos + teste com modelos reais pequenos). Nenhuma execução oficial ainda |
| Próximo passo | No Colab Pro: `indexar` → `rodar` → `avaliar` → `amostra-manual` (notebook, seções 4 a 7) |

---

## Sumário

1. [Como rodar no Google Colab](#1-como-rodar-no-google-colab)
2. [Como rodar localmente](#2-como-rodar-localmente)
3. [Etapas do pipeline](#3-etapas-do-pipeline)
4. [As seis configurações](#4-as-seis-configurações)
5. [Base de dados](#5-base-de-dados)
6. [Conjunto de teste e gabarito](#6-conjunto-de-teste-e-gabarito)
7. [Métricas e testes estatísticos](#7-métricas-e-testes-estatísticos)
8. [Registros: decisões, problemas e exclusões](#8-registros-decisões-problemas-e-exclusões)
9. [Estrutura do repositório](#9-estrutura-do-repositório)
10. [Reprodutibilidade, testes e ética](#10-reprodutibilidade-testes-e-ética)
11. [Limitações conhecidas](#11-limitações-conhecidas)

---

## 1. Como rodar no Google Colab

O caminho principal é o notebook [`notebooks/TCC_Colab.ipynb`](notebooks/TCC_Colab.ipynb), que clona este repositório, instala as dependências e guarda todos os dados no Google Drive.

1. Suba este repositório para o seu GitHub.
2. Abra o notebook no Colab (*Arquivo → Abrir notebook → GitHub*) e escolha uma GPU (*Ambiente de execução → Alterar o tipo de ambiente*; A100 ou L4 no Colab Pro).
3. Na segunda célula, ajuste `REPO_URL` e `DADOS` (pasta do Drive onde ficarão a base, os índices e os resultados).
4. Coloque a base já coletada (`base.sqlite`) na pasta `DADOS`, ou rode a coleta (célula opcional; leva horas e é retomável).
5. Rode as células em ordem.

Nenhum modelo usado exige token do Hugging Face. Se o Colab desconectar, basta rodar de novo: a coleta continua de onde parou, as saídas do LLM ficam em cache no banco e os índices ficam salvos no Drive.

## 2. Como rodar localmente

Requer Python 3.10 ou superior. Sem GPU, a coleta, o BM25 (C1) e os testes rodam bem; os modelos de C2–C6 rodam, mas devagar.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export TCC_DATA_DIR=$PWD/dados        # onde ficam base, índices e resultados
export PYTHONPATH=$PWD/src

python -m pytest                      # testes (base sintética, sem rede e sem GPU)
python -m tccrec --help               # lista de comandos
```

## 3. Etapas do pipeline

Todos os comandos são `python -m tccrec <comando>` e leem `config/config.yaml` (ou o arquivo passado em `--config`).

| # | Comando | O que faz | Saída principal |
| --- | --- | --- | --- |
| 1 | `coletar` | Candidatos no SteamSpy, reviews PT-BR, detalhes da loja, tags de usuário e seleção final. Retomável. `--etapa` roda uma etapa só. | tabelas `candidatos` a `selecionados` |
| 2 | `preparar-base` | Limpa os textos e gera os documentos das fontes A e B | tabela `documentos` |
| 3 | `validar-teste` | Confere cada par do conjunto de teste contra a base e descarta os inválidos | `validacao_conjunto_teste.csv` |
| 4 | `gabarito` | Gera as consultas e o gabarito (relevante, positivo, viola) | tabelas `consultas`, `qrels`; `qrels.tsv` |
| 5 | `indexar` | Índices BM25 e densos (ChromaDB) das duas fontes | `indices/` |
| 6 | `rodar` | Executa as configurações (`--configs`, `--fonte`, `--limite-consultas`) | tabelas `execucoes`, `rankings` |
| 7 | `avaliar` | Métricas, testes de P1, P2 e P3 e resumo em Markdown | `resultados/` |
| 8 | `amostra-manual` / `concordancia` | Amostra cega para conferir o gabarito e cálculo do kappa | `manual/` |
| 9 | `relatorio` / `exportar` | Relatórios da base e das exclusões; tabelas em CSV | `docs/`, `relatorios/`, `export/` |

## 4. As seis configurações

| Config | Funcionamento | Papel no estudo |
| --- | --- | --- |
| **C1** | BM25 sobre o pedido original (radicalizador Snowball do português; "sem" e "não" mantidos) | Base lexical |
| **C2** | Busca densa com `BAAI/bge-m3` no ChromaDB | Base semântica |
| **C3** | Híbrida: fusão de C1 e C2 por posição (RRF, k = 60) | Base mais usada em RAG; referência de P2 |
| **C4** | C3 + reranqueamento dos 50 primeiros com `BAAI/bge-reranker-v2-m3` | Tratamento implícito |
| **C5** | O LLM separa o pedido em alvo e parte excluída; nota = s(alvo) − β·s(excluída), como no E-SENS (β = 0,25, 0,5 e 1,0) | Tratamento explícito sem filtro |
| **C6** | O LLM extrai a exclusão como tags, títulos e desenvolvedoras; C3 sobre a consulta reescrita e filtro dos jogos com o atributo excluído | Tratamento explícito com filtro |
| *C6r* | Ablação de C6: só a reescrita da consulta, sem o filtro | Separa o efeito da reescrita do efeito do filtro |

As configurações com LLM usam o **mesmo** modelo (`Qwen/Qwen3-4B-Instruct-2507`, decodificação gulosa), para que a comparação meça a técnica e não o modelo. Cada configuração roda nas duas fontes:

* **Fonte A — `descricao_tags`:** um documento por jogo com nome, gêneros, tags (em inglês e com tradução própria), descrição curta e trecho do "sobre o jogo";
* **Fonte B — `reviews`:** um documento por review em português (até 50 por jogo); a nota do jogo é a melhor nota entre suas reviews (MaxP).

Os modelos são trocados em `config.yaml` (seção `modelos`). Os backends `fake` existem só para os testes automáticos e **não** devem ser usados em resultados.

## 5. Base de dados

A coleta usa apenas fontes públicas, sem chave de API:

| Dado | Fonte | Observação |
| --- | --- | --- |
| Candidatos | SteamSpy, endpoint `all`, e mais vendidos da loja brasileira | união das duas listas |
| Reviews em PT-BR | Steam, `IUserReviewsService/GetAppReviews` | as 100 mais úteis por jogo, compras na Steam |
| Descrição, gêneros, tipo, conteúdo | Steam, `appdetails` com `l=brazilian`, `cc=br` | jogos fora da loja brasileira são excluídos |
| Tags de usuário com votos | SteamSpy `appdetails`; reserva: página da loja | mesmos dados nas duas fontes |

Seleção final: jogos com pelo menos 30 reviews em português, em ordem decrescente desse volume, até 3000; excluem-se não jogos, conteúdo adulto explícito e jogos sem tags.

Os dados **não** ficam no Git (ver `.gitignore`): a pasta de dados esta no Drive.

## 6. Conjunto de teste e gabarito

O arquivo [`data/manual/conjunto_teste.yaml`](data/manual/conjunto_teste.yaml) tem 64 pares de pedidos, cada um com uma versão com exclusão e outra sem, em cinco tipos de restrição: estilo visual, dificuldade, modo de jogo, tema e título de referência.

```yaml
- id: EV01
  tipo: estilo_visual
  com: "jogos como Hollow Knight, mas sem pixel art"
  sem: "jogos como Hollow Knight"
  referencia: {nome: "Hollow Knight", appid: 367520}
  positivo: [["Metroidvania"]]          # todos os grupos; uma tag de cada grupo basta
  exclusao: {atributo: pixel_art}       # mapeado para tags no topo do arquivo
```

Também são aceitas exclusões por título (`titulos`, `padrao_titulo`) e por desenvolvedora (`desenvolvedor`). O gabarito sai das tags: o jogo é *positivo* se satisfaz a parte positiva, *viola* se tem o atributo, título ou desenvolvedora excluídos, e *relevante* se é positivo e, na versão com exclusão, não viola. O jogo citado no pedido é removido dos rankings.

O comando `validar-teste` descarta pares sem resposta possível ou em que a exclusão não "morde" e registra o motivo. 

## 7. Métricas e testes estatísticos

Métricas por consulta, com k = 5 como principal (3 e 10 também são calculados):

| Métrica | Significado |
| --- | --- |
| `P@k` | fração dos k primeiros que são relevantes |
| `nDCG@k` | qualidade da ordenação, relevância binária |
| `Hit@k` | há ao menos um relevante nos k primeiros |
| `Viol@k` | fração dos k primeiros que violam a exclusão |
| `AnyViol@k` | há ao menos um violador nos k primeiros |

Recall@k não é a métrica principal porque as consultas têm dezenas a centenas de relevantes. Os testes são de Wilcoxon pareados por consulta, com correção de Holm e IC 95% por bootstrap. Os resultados saem em `DADOS/resultados/`, com um `RESUMO.md` pronto para a monografia.


## 8. Estrutura do repositório

```
config/config.yaml            parâmetros de coleta, base, teste, busca, modelos e avaliação
data/manual/                  conjunto de teste (YAML) e tradução própria das tags (CSV)
src/tccrec/
  coleta/                     cliente HTTP educado com cache e coletores Steam/SteamSpy
  base/                       limpeza de texto e geração dos documentos
  teste/                      conjunto de teste, validação e gabarito
  busca/                      índices, modelos, prompts e as configurações C1–C6
  avaliacao/                  métricas, estatística, avaliação e conferência manual
  relatorios.py               relatórios e exportação
tests/                        testes com base sintética
```

## 10. Reprodutibilidade, testes e ética

* Todas as respostas HTTP da coleta ficam em `respostas_brutas.sqlite`; cada execução guarda o hash da configuração e os modelos usados; as saídas do LLM ficam em cache; as sementes são fixas (D-026).
* `python -m pytest` roda o pipeline inteiro numa base sintética, com modelos simulados, em poucos segundos.
* Não são coletados identificadores dos autores das reviews, e o texto das reviews não vai para o repositório (D-027).


