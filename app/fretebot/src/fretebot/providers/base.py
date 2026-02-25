from abc import ABC, abstractmethod
from fretebot.models import Cotacao

class ProviderBase(ABC):
    def __init__(self, nome: str) -> None:
        self.nome = nome
    
    @abstractmethod
    async def coteir(self, origem: str, destino: str, peso: float, valor: float) -> Cotacao | None:
        pass
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.nome})"
