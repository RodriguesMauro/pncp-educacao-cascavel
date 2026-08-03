#!/usr/bin/env python3
"""
Consulta a API pública do PNCP por novas licitações da Secretaria Municipal
de Educação de Cascavel-PR e grava um resumo em texto simples.

Roda via GitHub Actions (cron). Não requer autenticação.
"""
import json
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

CNPJ = "76208867000107"
CODIGO_UNIDADE_EDUCACAO = "07001"
MODALIDADES = [1, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13]
BASE_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
OUTPUT_FILE = "licitacoes-educacao-cascavel.txt"


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "")
        if unicodedata.category(c) != "Mn"
    )


def eh_secretaria_educacao(item: dict) -> bool:
    unidade = item.get("unidadeOrgao") or {}
    nome = strip_accents((unidade.get("nomeUnidade") or "")).lower()
    codigo = str(unidade.get("codigoUnidade") or "")
    return "educ" in nome or codigo == CODIGO_UNIDADE_EDUCACAO


def buscar_pagina(url: str, tentativas: int = 5):
    """Busca uma página da API com retry/backoff para erros 429."""
    espera = 2
    for tentativa in range(tentativas):
        try:
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=30) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 and tentativa < tentativas - 1:
                print(f"429 recebido, aguardando {espera}s e tentando de novo...")
                time.sleep(espera)
                espera *= 2
                continue
            print(f"Erro HTTP {e.code}: {e}")
            return None
        except URLError as e:
            print(f"Erro de rede: {e}")
            return None
        except json.JSONDecodeError:
            return None
    return None


def buscar_modalidade(data_inicial: str, data_final: str, modalidade: int) -> list:
    resultados = []
    pagina = 1
    while True:
        url = (
            f"{BASE_URL}?dataInicial={data_inicial}&dataFinal={data_final}"
            f"&codigoModalidadeContratacao={modalidade}&cnpj={CNPJ}&pagina={pagina}"
        )
        data = buscar_pagina(url)
        if data is None:
            break

        items = data.get("data") or []
        resultados.extend(items)

        total_paginas = data.get("totalPaginas") or 1
        if pagina >= total_paginas:
            break
        pagina += 1
        time.sleep(1)

    return resultados


def formatar_valor(v):
    if v is None:
        return "não informado"
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return str(v)


def main():
    hoje = datetime.now(timezone.utc).date()
    inicio = hoje - timedelta(days=3)
    data_inicial = inicio.strftime("%Y%m%d")
    data_final = hoje.strftime("%Y%m%d")

    todos = []
    for modalidade in MODALIDADES:
        todos.extend(buscar_modalidade(data_inicial, data_final, modalidade))
        time.sleep(1.5)

    encontrados = [item for item in todos if eh_secretaria_educacao(item)]

    linhas = []
    linhas.append(
        f"Licitações da Secretaria Municipal de Educação de Cascavel-PR "
        f"({inicio.strftime('%d/%m/%Y')} a {hoje.strftime('%d/%m/%Y')})"
    )
    linhas.append(f"Atualizado em: {datetime.now(timezone.utc).isoformat()}")
    linhas.append("")

    if not encontrados:
        linhas.append(
            "Nenhuma licitação nova da Secretaria de Educação encontrada no período."
        )
    else:
        for item in encontrados:
            linhas.append("-" * 60)
            linhas.append(f"Número controle PNCP: {item.get('numeroControlePNCP', 'n/d')}")
            linhas.append(f"Número da compra: {item.get('numeroCompra', 'n/d')}")
            linhas.append(f"Modalidade: {item.get('modalidadeNome', 'n/d')}")
            linhas.append(f"Objeto: {item.get('objetoCompra', 'n/d')}")
            linhas.append(f"Abertura proposta: {item.get('dataAberturaProposta', 'n/d')}")
            linhas.append(f"Encerramento proposta: {item.get('dataEncerramentoProposta', 'n/d')}")
            linhas.append(f"Valor total estimado: {formatar_valor(item.get('valorTotalEstimado'))}")
            link = item.get("linkSistemaOrigem")
            if link:
                linhas.append(f"Link: {link}")

    conteudo = "\n".join(linhas) + "\n"

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(conteudo)

    print(conteudo)


if __name__ == "__main__":
    main()
