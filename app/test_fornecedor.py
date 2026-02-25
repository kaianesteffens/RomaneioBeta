"""
Teste completo do modo fornecedor - executa cotação real em todas as transportadoras.

Dados de teste:
  CNPJ fornecedor: 05.892.834/0001-72
  CEP fornecedor:  95082-000 (Caxias do Sul/RS)
  2 volumes, 150x100x120 cm, 800 kg total, R$ 17.552,50
"""
import asyncio
import sys
import os

# Reproduz exatamente o que _montar_romaneio_fornecedor gera na UI
CNPJ_EMPRESA = "40223106000179"
CEP_EMPRESA = "99740000"

CNPJ_FORNECEDOR = "10620811000149"
CEP_FORNECEDOR = "14406077"

QTD = 3
ALT_CM = 44
LARG_CM = 44
COMP_CM = 144
PESO_TOTAL = 57.0
VALOR = 1582.50

peso_caixa = PESO_TOTAL / QTD  # 400.0
cubagem_unit = (ALT_CM * LARG_CM * COMP_CM) / 1_000_000  # 1.8
cubagem_total = cubagem_unit * QTD  # 3.6

c = CNPJ_EMPRESA
cnpj_fmt = f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
cep_fmt = f"{CEP_EMPRESA[:5]}-{CEP_EMPRESA[5:]}"

ROMANEIO = "\n".join([
    f"CNPJ/CPF: {cnpj_fmt}",
    f"CEP: {cep_fmt}",
    f"- VOL: {QTD}",
    f"- CUBAGEM: {cubagem_total:.6f} m3",
    f"- PESO: {PESO_TOTAL:.2f} kg",
    f"- TOTAL: R$ {VALOR:.2f}",
    f"{QTD} x Volume fornecedor - {peso_caixa:.3f} kg - {cubagem_unit:.6f} m3 - {ALT_CM}x{LARG_CM}x{COMP_CM}",
])


async def main():
    print("=" * 60)
    print("TESTE COMPLETO - MODO FORNECEDOR")
    print("=" * 60)
    print(f"\nCNPJ Fornecedor: {CNPJ_FORNECEDOR}")
    print(f"CEP Fornecedor:  {CEP_FORNECEDOR}")
    print(f"Volumes: {QTD}  Dims: {ALT_CM}x{LARG_CM}x{COMP_CM} cm")
    print(f"Peso: {PESO_TOTAL} kg  Valor: R$ {VALOR:.2f}")
    print(f"\nRomaneio gerado:\n{ROMANEIO}")
    print("=" * 60)

    from cotacao_transportadoras import (
        cotar_transportadoras_romaneio_colado,
        formatar_resultados_cotacao,
    )

    def progresso(payload):
        r = payload.get("resultado")
        done = payload.get("concluidas", 0)
        total = payload.get("total", 0)
        if r:
            nome = getattr(r, "transportadora", "?")
            status = getattr(r, "status", "?")
            vf = getattr(r, "valor_frete", None)
            prazo = getattr(r, "prazo_dias", None)
            det = getattr(r, "detalhes", "")
            if status == "ok" and vf is not None:
                print(f"  [{done}/{total}] {nome}: R$ {vf:.2f} | {prazo} dia(s)")
            else:
                print(f"  [{done}/{total}] {nome}: {status} - {det}")

    print("\nIniciando cotações...\n")
    resultados = await cotar_transportadoras_romaneio_colado(
        romaneio_colado=ROMANEIO,
        cep_origem=CEP_FORNECEDOR,
        cnpj_remetente=CNPJ_FORNECEDOR,
        tipo_frete="2",
        progresso_callback=progresso,
    )

    print("\n" + "=" * 60)
    resumo = formatar_resultados_cotacao(resultados)
    print(resumo)
    print("=" * 60)

    # Cleanup
    for r in resultados:
        prov = getattr(r, "_provider", None)
        if prov and hasattr(prov, "cleanup"):
            try:
                await prov.cleanup()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
