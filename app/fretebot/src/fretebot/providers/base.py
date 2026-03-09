from abc import ABC, abstractmethod
from fretebot.models import Cotacao
from fretebot.logging_conf import get_logger

_base_logger = get_logger(__name__)


async def launch_browser_resilient(playwright, *, headless: bool = True, args: list[str] | None = None):
    """Lança Chrome local via Playwright (channel='chrome')."""
    launch_args = args or ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
    return await playwright.chromium.launch(
        channel="chrome",
        headless=headless,
        args=launch_args,
    )


class ProviderBase(ABC):
    def __init__(self, nome: str) -> None:
        self.nome = nome
    
    @abstractmethod
    async def coteir(self, origem: str, destino: str, peso: float, valor: float) -> Cotacao | None:
        pass
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.nome})"
