"""Testes offline do monitor (sem rede): python3 tests/test_monitor_sagres.py"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import monitor_sagres as m  # noqa: E402

AGORA = datetime(2026, 10, 2, 11, 0, tzinfo=m.BRT)


def mk(cnpj, seq, objeto, enc, modal="Pregão - Eletrônico", sit="Divulgada no PNCP",
       razao="MUNICIPIO DE TESTE", num="10", ano=2026, upd="2026-10-01T10:00:00", srp=False):
    return {
        "numeroControlePNCP": f"{cnpj}-1-{seq:06d}/{ano}",
        "orgaoEntidade": {"cnpj": cnpj, "razaoSocial": razao},
        "unidadeOrgao": {"ufSigla": "PR", "municipioNome": "Teste", "nomeUnidade": "Secretaria"},
        "objetoCompra": objeto, "modalidadeNome": modal, "situacaoCompraNome": sit,
        "dataAberturaProposta": "2026-10-01T08:00:00", "dataEncerramentoProposta": enc,
        "dataAtualizacao": upd, "valorTotalEstimado": 120000.0, "numeroCompra": num,
        "anoCompra": ano, "sequencialCompra": seq, "srp": srp,
    }


def teste_classificacao():
    c = lambda txt: m.classificar(mk("1", 1, txt, "x"))[0]
    # deve casar
    assert c("Contratação de sistema de gestão escolar em nuvem") == "forte"
    assert c("Plataforma educacional SaaS") == "forte"
    assert c("Plataforma Integrada de Gestão Educacional com fornecimento de tablets") == "forte"
    assert c("Licença de uso de software para a rede de ensino") == "media"
    assert c("Licença de uso de sistema informatizado de gestão para a Secretaria de Educação") == "media"
    # ruído real visto no primeiro relatório: NÃO pode casar
    assert c("Fornecimento de cadernos personalizados destinados aos alunos da rede municipal de ensino") is None
    assert c("Aquisição de tablets, lousas mágicas e bolsas térmicas para a rede municipal de ensino") is None
    assert c("Contratação de Sistema Apostilado de Ensino para a Rede Municipal de Ensino") is None
    assert c("Preparo e fornecimento de refeições destinadas à alimentação escolar") is None
    assert c("Aquisição de uniformes e calçados escolares para alunos da rede municipal de ensino") is None
    assert c("Aquisição de brinquedos pedagógicos para a educação infantil") is None
    assert c("Aquisição de notebook e software para escolas") is None
    assert c("Aquisição de merenda escolar") is None
    assert c("Material escolar para os alunos da Rede Municipal de Ensino") is None
    # termo genérico sozinho não é mais aderência forte
    assert c("Serviços diversos para a rede municipal de ensino") is None


def faz_fetch(tabela):
    """Simula a API: devolve itens conforme o endpoint/parâmetros."""
    def fetch(url):
        for chave, itens in tabela.items():
            if chave in url:
                pagina = int(url.split("pagina=")[1].split("&")[0])
                return {"data": itens if pagina == 1 else [], "totalPaginas": 1}
        return {"data": [], "totalPaginas": 0}
    return fetch


def teste_fluxo():
    futuro = (AGORA + timedelta(days=5)).isoformat()
    passado = (AGORA - timedelta(days=17)).isoformat()
    ciedepar = mk(m.CIEDEPAR_CNPJ, 6, "Cessão de uso de Plataforma Integrada de Gestão Educacional SaaS",
                  passado, razao="CIEDEPAR - CONSORCIO INTERMUNICIPAL DE EDUCACAO", num="006", srp=True)
    aberta = mk("76000000000100", 7, "Contratação de sistema de gestão escolar", futuro)
    media = mk("76000000000200", 8, "Licença de uso de software para alunos e professores", futuro,
               modal="Dispensa")
    ruido = mk("76000000000300", 9, "Aquisição de mobiliário escolar", futuro)
    tabela = {
        "contratacoes/proposta": [aberta, media, ruido],
        f"cnpj={m.CIEDEPAR_CNPJ}": [ciedepar],
    }
    d = tempfile.mkdtemp()
    est, rel = os.path.join(d, "state/seen.json"), os.path.join(d, "reports")

    r1 = m.executar(["PR"], 3, 5, est, rel, fake := faz_fetch(tabela), AGORA)
    ids = set(r1["cand"])
    assert ciedepar["numeroControlePNCP"] in ids and aberta["numeroControlePNCP"] in ids
    assert ruido["numeroControlePNCP"] not in ids, "ruído não pode entrar"
    assert r1["cand"][ciedepar["numeroControlePNCP"]]["prio"] == 0
    assert r1["cand"][aberta["numeroControlePNCP"]]["prio"] == 1
    assert r1["cand"][media["numeroControlePNCP"]]["prio"] == 3
    # 1ª execução: alerta prio<=2 (CIEDEPAR + aberta forte), não a média
    assert len(r1["alertas"]) == 2, len(r1["alertas"])
    assert "Pregão Eletrônico 006/2026" in open(os.path.join(rel, "latest.md")).read()

    # 2ª execução igual: nada novo
    r2 = m.executar(["PR"], 3, 5, est, rel, fake, AGORA + timedelta(hours=4))
    assert r2["alertas"] == [] and open(os.path.join(rel, "alerts.md")).read() == ""

    # reviravolta: CIEDEPAR é remarcado/reaberto -> deve alertar como ALTERADO
    novo = dict(ciedepar, situacaoCompraNome="Suspensa",
                dataEncerramentoProposta=(AGORA + timedelta(days=10)).isoformat(),
                dataAtualizacao="2026-10-03T09:00:00")
    tabela2 = dict(tabela, **{f"cnpj={m.CIEDEPAR_CNPJ}": [novo]})
    r3 = m.executar(["PR"], 3, 5, est, rel, faz_fetch(tabela2), AGORA + timedelta(hours=8))
    assert len(r3["alertas"]) == 1 and "ALTERADO" in r3["alertas"][0]
    assert r3["cand"][ciedepar["numeroControlePNCP"]]["abertas"] is True
    txt = open(os.path.join(rel, "alerts.md")).read()
    assert "Suspensa" in txt and "ABERTAS" in txt

    # novo edital aparece depois -> alerta NOVO
    nova = mk("76000000000400", 11, "Plataforma educacional multi-município SaaS", futuro)
    tabela3 = dict(tabela2, **{"contratacoes/proposta": [aberta, media, ruido, nova]})
    r4 = m.executar(["PR"], 3, 5, est, rel, faz_fetch(tabela3), AGORA + timedelta(hours=12))
    assert len(r4["alertas"]) == 1 and "NOVO" in r4["alertas"][0]


def teste_diagnostico_ciedepar_ausente():
    d = tempfile.mkdtemp()
    m.executar(["PR"], 3, 5, os.path.join(d, "s.json"), os.path.join(d, "r"),
               faz_fetch({}), AGORA)
    txt = open(os.path.join(d, "r", "latest.md")).read()
    assert "devolveu 0 processo(s)" in txt and "BLL Compras" in txt


def teste_api_fora_do_ar():
    d = tempfile.mkdtemp()
    r = m.executar(["PR"], 3, 5, os.path.join(d, "s.json"), os.path.join(d, "r"),
                   lambda url: None, AGORA)
    assert r["cand"] == {}
    assert "não foi localizado" in open(os.path.join(d, "r", "latest.md")).read()


if __name__ == "__main__":
    m.time.sleep = lambda s: None  # acelera
    teste_classificacao()
    teste_fluxo()
    teste_diagnostico_ciedepar_ausente()
    teste_api_fora_do_ar()
    print("OK: todos os testes passaram")
