"""Teste de rastreamento com XMLs da pasta Downloads/NFes."""
import asyncio
import sys
from pathlib import Path

# Garante imports do app
sys.path.insert(0, str(Path(__file__).parent / "app"))
sys.path.insert(0, str(Path(__file__).parent / "app" / "fretio" / "src"))

from extrator_nfe import extrair_xml, identificar_transportadora
from rastreamento import rastrear_nfe

NFE_DIR = Path(r"C:\Users\eduardo\Downloads\NFes")


async def main():
    xmls = sorted(NFE_DIR.glob("*.xml"))
    # Ignora XMLs de cancelamento
    xmls = [x for x in xmls if "cancelamento" not in x.name.lower()]
    print(f"Total XMLs: {len(xmls)}\n")

    resultados = []
    for xml_path in xmls:
        nfs = extrair_xml(xml_path)
        if not nfs:
            print(f"[SKIP] {xml_path.name} — sem NF extraída")
            continue
        nf = nfs[0]
        transp = identificar_transportadora(nf)
        if transp != "braspress":
            print(f"[SKIP] NF {nf.numero} — transportadora: {transp or nf.transportadora_nome or 'N/A'}")
            continue

        print(f"[TEST] NF {nf.numero} | CNPJ emitente: {nf.emitente_cnpj} | transp: braspress")
        res = await rastrear_nfe(
            transportadora="braspress",
            numero_nfe=nf.numero,
            cnpj_emitente=nf.emitente_cnpj,
            chave_acesso=nf.chave_acesso,
        )
        status = f"{'ENTREGUE' if res.entregue else 'EM TRANSITO'} | {res.status_texto}"
        link = res.link_rastreio
        resultados.append((nf.numero, status, link))
        print(f"       → {status}")
        print(f"       link: {link}\n")

    print("=" * 60)
    print(f"Braspress testadas: {len(resultados)}")
    entregues = sum(1 for _, s, _ in resultados if "ENTREGUE" in s)
    bloqueados = sum(1 for _, s, _ in resultados if "bloqueado" in s.lower())
    print(f"  Entregues : {entregues}")
    print(f"  Bloqueados: {bloqueados}")
    print(f"  Em trânsito: {len(resultados) - entregues - bloqueados}")


if __name__ == "__main__":
    asyncio.run(main())
