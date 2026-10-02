#!/usr/bin/env python3
"""
Monitor de oportunidades para a plataforma Sagres (gestão educacional) no PNCP.

O que faz a cada execução:
  1. ABERTAS  - lista contratações com propostas ainda abertas (endpoint /proposta)
                nas UFs monitoradas e filtra por palavras-chave de gestão educacional.
  2. RECENTES - lista contratações publicadas nos últimos dias (endpoint /publicacao),
                para pegar dispensas/inexigibilidades e prazos curtos.
  3. WATCH    - acompanha o CIEDEPAR (Pregão 006/2026 e qualquer outro processo) e
                outros consórcios: detecta mudança de situação/datas (suspensão,
                retificação, republicação, remarcação) e novas atas de registro de preços.
  4. Compara com o estado anterior (state/seen.json) e escreve:
        reports/latest.md  - relatório completo ordenado por prioridade
        reports/alerts.md  - SÓ o que é novo ou mudou (vazio = nada novo)

Somente biblioteca padrão. Não precisa de autenticação.
Configuração opcional por variáveis de ambiente:
  MONITOR_UFS          UFs separadas por vírgula (padrão: PR)
  MONITOR_DIAS_RECENTES janela de publicação recente em dias (padrão: 3)
  MONITOR_MAX_PAGINAS  limite de páginas por consulta (padrão: 60)
"""
import hashlib
import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://pncp.gov.br/api/consulta/v1"
BRT = timezone(timedelta(hours=-3))

# 2 diálogo competitivo, 4 concorrência eletrônica, 5 concorrência presencial,
# 6 pregão eletrônico, 7 pregão presencial, 8 dispensa, 9 inexigibilidade, 12 credenciamento
MODALIDADES = [2, 4, 5, 6, 7, 8, 9, 12]

CIEDEPAR_CNPJ = "37584270000174"
# Consórcios adicionais a vigiar (CNPJ só números). Acrescente aqui quando descobrir.
CONSORCIOS_WATCH = {
    CIEDEPAR_CNPJ: "CIEDEPAR",
}

# ---------------------------------------------------------------- palavras-chave
FORTES = [
    "gestao educacional", "gestao escolar", "gestao da educacao",
    "sistema de gestao educacional", "sistema de gestao escolar",
    "sistema educacional", "plataforma educacional",
    "plataforma integrada de gestao", "sistema academico",
    "diario de classe", "diario escolar", "diario eletronico",
    "secretaria escolar", "matricula online", "matricula digital",
    "software educacional", "software de gestao escolar",
    "sistema de gestao da educacao", "gestao pedagogica",
    "educacao municipal", "rede municipal de ensino",
]
TIPO_SOLUCAO = [
    "software", "sistema", "plataforma", "saas", "licenca de uso",
    "cessao de uso", "licenciamento", "aplicativo", "solucao tecnologica",
    "informatizado", "tecnologia da informacao",
]
CONTEXTO_EDU = [
    "educac", "escola", "escolar", "ensino", "pedagog", "academic",
    "aluno", "estudante", "professor", "docente", "secretaria de educacao",
]
RUIDO = [
    "mobiliario", "merenda", "alimentacao", "genero alimenticio", "uniforme",
    "veiculo", "onibus", "construcao", "reforma", "ar condicionado",
    "computador", "notebook", "tablet", "chromebook", "lousa", "livros",
    "material escolar", "material didatico", "brinquedo", "playground",
    "impressora", "limpeza", "vigilancia",
]


def strip_accents(s):
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "")
        if unicodedata.category(c) != "Mn"
    )


def norm(s):
    return strip_accents(s or "").lower()


def classificar(item):
    """Retorna (nivel, motivos). nivel: 'forte', 'media' ou None."""
    texto = norm(item.get("objetoCompra")) + " " + norm(item.get("informacaoComplementar"))
    fortes = [k for k in FORTES if k in texto]
    if fortes:
        return "forte", fortes
    tipo = [k for k in TIPO_SOLUCAO if k in texto]
    ctx = [k for k in CONTEXTO_EDU if k in texto]
    if tipo and ctx and not any(r in texto for r in RUIDO):
        return "media", tipo[:2] + ctx[:2]
    return None, []


# ---------------------------------------------------------------- HTTP
def http_json(url, tentativas=5):
    espera = 2
    for t in range(tentativas):
        try:
            req = Request(url, headers={"Accept": "application/json",
                                        "User-Agent": "monitor-sagres/1.0"})
            with urlopen(req, timeout=60) as resp:
                if resp.status == 204:
                    return {"data": [], "totalPaginas": 0}
                corpo = resp.read().decode("utf-8")
                return json.loads(corpo) if corpo.strip() else {"data": [], "totalPaginas": 0}
        except HTTPError as e:
            if e.code in (404, 204):
                return {"data": [], "totalPaginas": 0}
            if e.code in (429, 500, 502, 503, 504) and t < tentativas - 1:
                time.sleep(espera)
                espera *= 2
                continue
            print(f"    HTTP {e.code} em {url}", flush=True)
            return None
        except (URLError, TimeoutError, OSError) as e:
            if t < tentativas - 1:
                time.sleep(espera)
                espera *= 2
                continue
            print(f"    rede/timeout em {url}: {e}", flush=True)
            return None
        except json.JSONDecodeError:
            return None
    return None


def paginar(endpoint, params, fetch=http_json, max_paginas=60, tamanho=50):
    """Percorre todas as páginas. Se tamanhoPagina for recusado (400), tenta sem ele."""
    itens, pagina, usar_tamanho = [], 1, True
    while pagina <= max_paginas:
        p = dict(params, pagina=pagina)
        if usar_tamanho:
            p["tamanhoPagina"] = tamanho
        data = fetch(f"{BASE}/{endpoint}?{urlencode(p)}")
        if data is None and usar_tamanho and pagina == 1:
            usar_tamanho = False
            continue
        if data is None:
            break
        itens.extend(data.get("data") or [])
        if pagina >= (data.get("totalPaginas") or 1):
            break
        pagina += 1
        time.sleep(0.3)
    return itens


# ---------------------------------------------------------------- coleta
def item_id(item):
    return item.get("numeroControlePNCP") or (
        f"{(item.get('orgaoEntidade') or {}).get('cnpj')}-"
        f"{item.get('anoCompra')}-{item.get('sequencialCompra')}"
    )


def link_pncp(item):
    cnpj = (item.get("orgaoEntidade") or {}).get("cnpj")
    ano, seq = item.get("anoCompra"), item.get("sequencialCompra")
    return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}" if cnpj and ano and seq else ""


def assinatura(item):
    """Hash dos campos que, se mudarem, indicam reviravolta no processo."""
    campos = [
        item.get("situacaoCompraNome"), item.get("situacaoCompraId"),
        item.get("dataAberturaProposta"), item.get("dataEncerramentoProposta"),
        item.get("dataAtualizacao"), item.get("valorTotalEstimado"),
        item.get("objetoCompra"), item.get("modoDisputaNome"),
    ]
    return hashlib.sha1(json.dumps(campos, ensure_ascii=False).encode()).hexdigest()[:12]


def coletar_abertas(ufs, hoje, fetch=http_json, max_paginas=60):
    achados = {}
    data_final = (hoje + timedelta(days=365)).strftime("%Y%m%d")
    for uf in ufs:
        for mod in MODALIDADES:
            print(f"[abertas] {uf} modalidade {mod}", flush=True)
            for it in paginar("contratacoes/proposta",
                              {"dataFinal": data_final, "codigoModalidadeContratacao": mod, "uf": uf},
                              fetch, max_paginas):
                achados[item_id(it)] = it
    return achados


def coletar_recentes(ufs, hoje, dias, fetch=http_json, max_paginas=60):
    achados = {}
    ini = (hoje - timedelta(days=dias)).strftime("%Y%m%d")
    fim = hoje.strftime("%Y%m%d")
    for uf in ufs:
        for mod in MODALIDADES:
            print(f"[recentes] {uf} modalidade {mod}", flush=True)
            for it in paginar("contratacoes/publicacao",
                              {"dataInicial": ini, "dataFinal": fim,
                               "codigoModalidadeContratacao": mod, "uf": uf},
                              fetch, max_paginas):
                achados[item_id(it)] = it
    return achados


def coletar_watch(hoje, fetch=http_json, max_paginas=30):
    """Tudo que os consórcios vigiados publicaram nos últimos 150 dias + atas."""
    processos, atas = {}, []
    ini = (hoje - timedelta(days=150)).strftime("%Y%m%d")
    fim = hoje.strftime("%Y%m%d")
    for cnpj, nome in CONSORCIOS_WATCH.items():
        for mod in [2, 4, 5, 6, 7, 8, 9, 12]:
            print(f"[watch {nome}] modalidade {mod}", flush=True)
            for it in paginar("contratacoes/publicacao",
                              {"dataInicial": ini, "dataFinal": fim,
                               "codigoModalidadeContratacao": mod, "cnpj": cnpj},
                              fetch, max_paginas):
                it["_watch"] = nome
                processos[item_id(it)] = it
        print(f"[watch {nome}] atas", flush=True)
        try:
            for a in paginar("atas", {"dataInicial": ini, "dataFinal": fim, "cnpj": cnpj},
                             fetch, 10):
                a["_watch"] = nome
                atas.append(a)
        except Exception as e:  # endpoint de atas é acessório; nunca derruba o monitor
            print(f"    atas indisponíveis: {e}", flush=True)
    return processos, atas


# ---------------------------------------------------------------- análise
def parse_dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=BRT)
    except ValueError:
        return None


def propostas_abertas(item, agora):
    enc = parse_dt(item.get("dataEncerramentoProposta"))
    return enc is not None and enc >= agora


def eh_pregao_ciedepar_006(item):
    org = (item.get("orgaoEntidade") or {}).get("cnpj")
    if org != CIEDEPAR_CNPJ:
        return False
    num = "".join(c for c in str(item.get("numeroCompra") or "") if c.isdigit())
    obj = norm(item.get("objetoCompra"))
    return (str(item.get("anoCompra")) == "2026" and num.lstrip("0") == "6") or \
           "gestao educacional" in obj


def montar_candidatos(abertas, recentes, watch, agora):
    cand = {}
    for origem, base in (("abertas", abertas), ("recentes", recentes), ("watch", watch)):
        for iid, it in base.items():
            nivel, motivos = classificar(it)
            org = it.get("orgaoEntidade") or {}
            razao = norm(org.get("razaoSocial"))
            eh_consorcio = "consorcio" in razao or bool(it.get("_watch"))
            if not nivel and not it.get("_watch") and not (eh_consorcio and
                    any(c in norm(it.get("objetoCompra")) for c in CONTEXTO_EDU)):
                continue
            reg = cand.setdefault(iid, {"item": it, "origens": set(), "nivel": nivel,
                                        "motivos": motivos})
            reg["origens"].add(origem)
            if nivel == "forte" or reg["nivel"] is None:
                reg["nivel"], reg["motivos"] = nivel or reg["nivel"], motivos or reg["motivos"]
    for iid, reg in cand.items():
        it = reg["item"]
        reg["abertas"] = propostas_abertas(it, agora)
        reg["consorcio"] = bool(it.get("_watch")) or "consorcio" in norm(
            (it.get("orgaoEntidade") or {}).get("razaoSocial"))
        reg["ciedepar006"] = eh_pregao_ciedepar_006(it)
        reg["srp"] = bool(it.get("srp"))
        # prioridade: 0 = máxima
        if reg["ciedepar006"]:
            reg["prio"] = 0
        elif reg["abertas"] and reg["nivel"] == "forte":
            reg["prio"] = 1
        elif reg["consorcio"] and (reg["abertas"] or reg["nivel"]):
            reg["prio"] = 2
        elif reg["abertas"] and reg["nivel"] == "media":
            reg["prio"] = 3
        elif reg["nivel"] == "forte":
            reg["prio"] = 4
        else:
            reg["prio"] = 5
    return cand


ROTULO_PRIO = {0: "🔴 CIEDEPAR 006/2026", 1: "🔴 Aberta · aderência forte",
               2: "🟠 Consórcio", 3: "🟡 Aberta · aderência média",
               4: "⚪ Aderência forte · fora do prazo", 5: "⚪ Acompanhamento"}


def fmt_valor(v):
    try:
        return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "não informado"


def fmt_dt(s):
    d = parse_dt(s)
    return d.astimezone(BRT).strftime("%d/%m/%Y %H:%M") if d else "n/d"


def bloco(iid, reg, motivo_alerta=None):
    it = reg["item"]
    uni = it.get("unidadeOrgao") or {}
    org = it.get("orgaoEntidade") or {}
    linhas = [
        f"### {ROTULO_PRIO[reg['prio']]} — {org.get('razaoSocial', 'n/d')}"
        f" ({uni.get('municipioNome', 'n/d')}/{uni.get('ufSigla', 'n/d')})",
    ]
    if motivo_alerta:
        linhas.append(f"**{motivo_alerta}**")
    linhas += [
        f"- **Objeto:** {(it.get('objetoCompra') or 'n/d').strip()[:600]}",
        f"- **Modalidade:** {it.get('modalidadeNome', 'n/d')}"
        f" · nº {it.get('numeroCompra', 'n/d')}/{it.get('anoCompra', '')}"
        f"{' · SRP (ata de registro de preços)' if reg['srp'] else ''}",
        f"- **Situação:** {it.get('situacaoCompraNome', 'n/d')}"
        f" · atualizado em {fmt_dt(it.get('dataAtualizacao'))}",
        f"- **Propostas:** {fmt_dt(it.get('dataAberturaProposta'))} → "
        f"{fmt_dt(it.get('dataEncerramentoProposta'))}"
        f"{'  ✅ ABERTAS' if reg['abertas'] else ''}",
        f"- **Valor estimado:** {fmt_valor(it.get('valorTotalEstimado'))}",
        f"- **Casou com:** {', '.join(reg['motivos']) if reg['motivos'] else 'vigilância de consórcio'}",
        f"- **PNCP:** {link_pncp(it)}",
    ]
    if it.get("linkSistemaOrigem"):
        linhas.append(f"- **Sistema de origem:** {it['linkSistemaOrigem']}")
    return "\n".join(linhas) + "\n"


def cabecalho_ciedepar(cand, watch, agora):
    ach = [(i, r) for i, r in cand.items() if r["ciedepar006"]]
    linhas = ["## Vigilância CIEDEPAR — Pregão Eletrônico 006/2026", ""]
    if not ach:
        linhas += [
            "⚠️ O processo **não foi localizado** na API do PNCP nesta execução "
            "(pode não ter sido publicado lá, ou a consulta falhou). "
            "Confira na BLL Compras e em ciedepar.com.br.", ""]
        return "\n".join(linhas) + "\n"
    for iid, reg in ach:
        it = reg["item"]
        linhas += [
            f"- Situação: **{it.get('situacaoCompraNome', 'n/d')}** · "
            f"propostas até {fmt_dt(it.get('dataEncerramentoProposta'))} · "
            f"atualizado em {fmt_dt(it.get('dataAtualizacao'))}",
            f"- Propostas abertas agora? **{'SIM' if reg['abertas'] else 'não'}**",
            f"- {link_pncp(it)}", ""]
    return "\n".join(linhas) + "\n"


def carregar_estado(caminho):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def salvar(caminho, conteudo, binario_json=False):
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        if binario_json:
            json.dump(conteudo, f, ensure_ascii=False, indent=1, sort_keys=True)
        else:
            f.write(conteudo)


def comparar(cand, estado, agora_iso):
    """Devolve (novos, mudados, novo_estado)."""
    novo_estado, novos, mudados = {}, [], []
    for iid, reg in cand.items():
        it = reg["item"]
        sig = assinatura(it)
        antigo = estado.get(iid)
        novo_estado[iid] = {
            "sig": sig,
            "primeira_vez": (antigo or {}).get("primeira_vez", agora_iso),
            "ultima_vez": agora_iso,
            "situacao": it.get("situacaoCompraNome"),
        }
        if antigo is None:
            novos.append(iid)
        elif antigo.get("sig") != sig:
            mudados.append(iid)
    # mantém histórico de itens que sumiram da consulta (não alerta de novo se voltarem iguais)
    for iid, v in estado.items():
        novo_estado.setdefault(iid, v)
    return novos, mudados, novo_estado


def executar(ufs, dias, max_paginas, estado_path, relatorios_dir, fetch=http_json, agora=None):
    agora = agora or datetime.now(BRT)
    hoje = agora.date()
    abertas = coletar_abertas(ufs, hoje, fetch, max_paginas)
    recentes = coletar_recentes(ufs, hoje, dias, fetch, max_paginas)
    watch, atas = coletar_watch(hoje, fetch)

    cand = montar_candidatos(abertas, recentes, watch, agora)
    estado = carregar_estado(estado_path)
    primeira_execucao = not estado
    novos, mudados, novo_estado = comparar(cand, estado, agora.isoformat())

    ordem = sorted(cand, key=lambda i: (cand[i]["prio"],
                                        cand[i]["item"].get("dataEncerramentoProposta") or "9"))

    # ---- relatório completo
    rel = [f"# Monitor Sagres — {agora.strftime('%d/%m/%Y %H:%M')} (Brasília)", "",
           f"UFs: {', '.join(ufs)} · candidatos: {len(cand)} · "
           f"novos: {len(novos)} · alterados: {len(mudados)} · atas CIEDEPAR: {len(atas)}", "",
           cabecalho_ciedepar(cand, watch, agora)]
    for a in atas:
        rel.append(f"- 📜 Ata CIEDEPAR: {a.get('objetoContratacao') or a.get('numeroAtaRegistroPreco', 'n/d')}"
                   f" · vigência até {fmt_dt(a.get('vigenciaFim'))}")
    if atas:
        rel.append("")
    rel.append("## Oportunidades")
    rel.append("")
    for iid in ordem:
        rel.append(bloco(iid, cand[iid]))
    salvar(os.path.join(relatorios_dir, "latest.md"), "\n".join(rel))

    # ---- alertas (só novo/mudado). Na 1ª execução só alerta o que importa de verdade.
    alertas = []
    for iid in ordem:
        reg = cand[iid]
        if iid in novos:
            if primeira_execucao and reg["prio"] > 2:
                continue
            alertas.append(bloco(iid, reg, "🆕 NOVO"))
        elif iid in mudados:
            alertas.append(bloco(iid, reg, "♻️ ALTERADO (situação/datas/valor mudaram)"))
    texto_alerta = ""
    if alertas:
        texto_alerta = (f"# Monitor Sagres — {len(alertas)} alerta(s) em "
                        f"{agora.strftime('%d/%m/%Y %H:%M')}\n\n" + "\n".join(alertas))
    salvar(os.path.join(relatorios_dir, "alerts.md"), texto_alerta)
    salvar(estado_path, novo_estado, binario_json=True)
    print(f"Concluído: {len(cand)} candidatos, {len(novos)} novos, {len(mudados)} alterados, "
          f"{len(alertas)} alertas.")
    return {"cand": cand, "novos": novos, "mudados": mudados, "alertas": alertas}


def main():
    ufs = [u.strip().upper() for u in os.environ.get("MONITOR_UFS", "PR").split(",") if u.strip()]
    dias = int(os.environ.get("MONITOR_DIAS_RECENTES", "3"))
    max_pag = int(os.environ.get("MONITOR_MAX_PAGINAS", "60"))
    executar(ufs, dias, max_pag, "state/seen.json", "reports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
