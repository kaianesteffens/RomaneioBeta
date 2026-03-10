from fretebot.providers.base import ProviderBase
from fretebot.providers.braspress_playwright import BraspressPlaywrightProvider as BraspressProvider
from fretebot.providers.bauer_auto import BauerAutoProvider
from fretebot.providers.trd import TRDProvider
from fretebot.providers.agex import AGEXProvider
from fretebot.providers.eucatur import EucaturProvider
from fretebot.providers.rodonaves import RodonavesProvider
from fretebot.providers.alfa import AlfaProvider
from fretebot.providers.coopex import CoopexProvider

__all__ = [
    "ProviderBase",
    "BraspressProvider",
    "BauerAutoProvider",
    "TRDProvider",
    "AGEXProvider",
    "EucaturProvider",
    "RodonavesProvider",
    "AlfaProvider",
    "CoopexProvider",
]
