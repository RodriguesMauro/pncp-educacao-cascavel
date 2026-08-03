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

Para adicionar um novo município, inclua um item na lista `MUNICIPIOS` em
`pncp_educacao_municipios.py` com nome, CNPJ da prefeitura e nome do
arquivo de saída.
