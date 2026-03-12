"""
Teste completo do modo Calcular Frete - executa cotacao real em todas as
transportadoras habilitadas (incluindo Rodonaves).

Dados de teste:
  CNPJ destinatario: 10.620.811/0001-49
  CEP destino:       14406-077 (Franca/SP)
  3 volumes, 44x44x144 cm, 57 kg total, R$ 1.582,50
"""
import asyncio
import sys
import os

# Dados do destinatario (quem recebe)
CNPJ_DESTINATARIO = "10620811000149"
CEP_DESTINO = "14406077"

QTD = 3
ALT_CM = 44
LARG_CM = 44
COMP_CM = 144
PESO_TOTAL = 57.0
VALOR = 1582.50

peso_caixa = PESO_TOTAL / QTD
cubagem_unit = (ALT_CM * LARG_CM * COMP_CM) / 1_000_000
cubagem_total = cubagem_unit * QTD

c = CNPJ_DESTINATARIO
cnpj_fmt = f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
cep_fmt = f"{CEP_DESTINO[:5]}-{CEP_DESTINO[5:]}"

ROMANEIO = "\n".join([
    f"CNPJ/CPF: {cnpj_fmt}",
    f"CEP: {cep_fmt}",
    f"- VOL: {QTD}",
    f"- CUBAGEM: {cubagem_total:.6f} m3",
    f"- PESO: {PESO_TOTAL:.2f} kg",
    f"- TOTAL: R$ {VALOR:.2f}",
    f"{QTD} x Volume teste - {peso_caixa:.3f} kg - {cubagem_unit:.6f} m3 - {ALT_CM}x{LARG_CM}x{COMP_CM}",
])


async def main():
    print("=" * 60)
    print("TESTE COMPLETO - CALCULAR FRETE")
    print("=" * 60)
    print(f"\nCNPJ Destinatario: {cnpj_fmt}")
    print(f"CEP Destino:       {cep_fmt}")
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

    print("\nIniciando cotacoes...\n")
    resultados = await cotar_transportadoras_romaneio_colado(
        romaneio_colado=ROMANEIO,
        cep_origem="",
        cnpj_remetente="",
        tipo_frete="",
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
