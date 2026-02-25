from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class Pedido:
    origem: str
    destino: str
    peso: float
    valor: float
    altura: float
    largura: float
    profundidade: float
    
    def __post_init__(self) -> None:
        if self.peso <= 0:
            raise ValueError("Peso deve ser > 0")
        if any(x <= 0 for x in [self.altura, self.largura, self.profundidade]):
            raise ValueError("Dimensões devem ser > 0")

@dataclass
class Pesos:
    peso_real: float
    cubagem_m3: float
    peso_cubado: float
    peso_taxado: float

@dataclass
class Cotacao:
    transportadora: str
    prazo_dias: int
    valor_frete: float
    restricoes: Optional[str] = None
    timestamp: datetime = None
    
    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now()

@dataclass
class Relatorio:
    pedido: Pedido
    pesos: Pesos
    cotacoes: list[Cotacao]
    gerado_em: datetime
