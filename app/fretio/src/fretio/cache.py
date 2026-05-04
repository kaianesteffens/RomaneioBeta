import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

class Cache:
    def __init__(self, db_path: Path = Path("cache/frete.db")) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS cotacoes (
                    id INTEGER PRIMARY KEY,
                    chave TEXT UNIQUE,
                    origem TEXT,
                    destino TEXT,
                    peso REAL,
                    valor REAL,
                    resultado JSON,
                    criado_em TIMESTAMP
                )
            ''')
            conn.commit()
    
    @staticmethod
    def gerar_chave(origem: str, destino: str, peso: float, valor: float) -> str:
        return f"{origem.lower()}|{destino.lower()}|{peso}|{valor}"
    
    def buscar(self, chave: str, max_horas: int = 24) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT resultado, criado_em FROM cotacoes WHERE chave = ?", (chave,))
            row = c.fetchone()
        if not row:
            return None
        resultado, criado_em = row
        criado = datetime.fromisoformat(criado_em)
        if datetime.now() - criado > timedelta(hours=max_horas):
            return None
        return json.loads(resultado)
    
    def salvar(self, chave: str, origem: str, destino: str, peso: float, valor: float, resultado: dict) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cotacoes (chave, origem, destino, peso, valor, resultado, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chave, origem, destino, peso, valor, json.dumps(resultado), datetime.now().isoformat())
            )
            conn.commit()
