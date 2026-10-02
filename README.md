# pncp-educacao-cascavel

Consulta diária da API pública do PNCP (Portal Nacional de Contratações
Públicas) por novas licitações das Secretarias Municipais de Educação de
vários municípios do Paraná. Roda via GitHub Actions e grava um resumo em
texto simples por município.

## Municípios monitorados

- Cascavel — `licitacoes-educacao-cascavel.txt`
- Ponta Grossa — `licitacoes-educacao-ponta-grossa.txt`
- Foz do Iguaçu — `licitacoes-educacao-foz-do-iguacu.txt`
- Pato Branco — `licitacoes-educacao-pato-branco.txt`
- Londrina — `licitacoes-educacao-londrina.txt`
- Paranaguá — `licitacoes-educacao-paranagua.txt`
- Toledo — `licitacoes-educacao-toledo.txt`
- Curitiba - `licitacoes-educacao-curitiba.txt`
- Guarapuava - `licitacoes-educacao-guarapuava.txt`
 

Para adicionar um novo município, inclua um item na lista `MUNICIPIOS` em
`pncp_educacao_municipios.py` com nome, CNPJ da prefeitura e nome do
arquivo de saída.

---

## Monitor Sagres (gestão educacional)

`monitor_sagres.py` + `.github/workflows/monitor-sagres.yml` — roda 1x/dia
(08h Brasília, seg–sex) e também manualmente.

O que faz:

1. **Abertas** — contratações com propostas ainda abertas nas UFs monitoradas
   (`MONITOR_UFS`, padrão `PR`) que casam com palavras-chave de gestão educacional.
2. **Recentes** — publicadas nos últimos 3 dias (pega dispensas e prazos curtos).
3. **Vigilância de consórcios** — CIEDEPAR (Pregão 006/2026 e demais processos) e CISNORPI (só processos de educação, ex.: Pregão 36/2026)
   e novas atas de registro de preços. Detecta suspensão, retificação, remarcação
   ou reabertura de prazo comparando com `state/seen.json`.

Saídas:

- `reports/latest.md` — relatório completo, ordenado por prioridade.
- `reports/alerts.md` — só o que é novo ou mudou. Se não estiver vazio, o workflow
  abre uma **issue** no repositório (o GitHub envia e-mail).

Prioridades: 🔴 CIEDEPAR 006/2026 · 🔴 aberta com aderência forte · 🟠 consórcio ·
🟡 aberta com aderência média · ⚪ fora do prazo.

Ajustes: palavras-chave (`FORTES`, `TIPO_SOLUCAO`, `CONTEXTO_EDU`, `RUIDO`) e
consórcios vigiados (`CONSORCIOS_WATCH`) ficam no topo de `monitor_sagres.py`.
Testes offline: `python tests/test_monitor_sagres.py`.
