from fretebot.models import Pedido, Pesos

def calcular_cubagem(altura: float, largura: float, profundidade: float) -> float:
    return (altura * largura * profundidade) / 1_000_000

def calcular_pesos(pedido: Pedido, fator: float = 6000) -> Pesos:
    cubagem = calcular_cubagem(pedido.altura, pedido.largura, pedido.profundidade)
    peso_cubado = cubagem * fator
    peso_taxado = max(pedido.peso, peso_cubado)
    return Pesos(
        peso_real=pedido.peso,
        cubagem_m3=round(cubagem, 6),
        peso_cubado=round(peso_cubado, 2),
        peso_taxado=round(peso_taxado, 2),
    )
    