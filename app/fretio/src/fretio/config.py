from pathlib import Path
from dataclasses import dataclass

@dataclass
class Config:
    fator_cubagem: float = 6000
    cache_dir: Path = Path("cache")

CONFIG = Config()
