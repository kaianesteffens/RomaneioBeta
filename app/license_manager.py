"""
FreteBot — Gerador e Gerenciador de Licenças (uso do administrador).

Uso:
    python license_manager.py gerar "Nome do Cliente"
    python license_manager.py gerar "Nome do Cliente" --expires 2027-01-01
    python license_manager.py listar
    python license_manager.py bloquear FBOT-XXXX-XXXX-XXXX-XXXX
    python license_manager.py desbloquear FBOT-XXXX-XXXX-XXXX-XXXX    python license_manager.py desvincular FBOT-XXXX-XXXX-XXXX-XXXX  # libera a máquina vinculada    python license_manager.py bloquear-maquina <machine_id>
    python license_manager.py exportar                 # salva licenses.json
    python license_manager.py push                     # atualiza o gist remoto
"""
from __future__ import annotations

import json
import secrets
import string
import sys
from pathlib import Path
from typing import Optional

_LICENSES_FILE = Path(__file__).parent / "licenses_admin.json"


def _load_db() -> dict:
    """Carrega o banco de licenças local."""
    if _LICENSES_FILE.exists():
        return json.loads(_LICENSES_FILE.read_text(encoding="utf-8"))
    return {"licenses": {}, "blocked_keys": [], "blocked_machines": []}


def _save_db(db: dict) -> None:
    """Salva o banco de licenças local."""
    _LICENSES_FILE.write_text(
        json.dumps(db, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[OK] Salvo em {_LICENSES_FILE}")


def _gen_key() -> str:
    """Gera chave no formato FBOT-XXXX-XXXX-XXXX-XXXX."""
    chars = string.ascii_uppercase + string.digits
    parts = ["FBOT"]
    for _ in range(4):
        part = "".join(secrets.choice(chars) for _ in range(4))
        parts.append(part)
    return "-".join(parts)


def cmd_gerar(owner: str, expires: Optional[str] = None) -> str:
    """Gera uma nova licença."""
    db = _load_db()
    key = _gen_key()
    # Garantir unicidade
    while key in db["licenses"]:
        key = _gen_key()

    entry = {"owner": owner, "active": True, "machines": []}
    if expires:
        entry["expires"] = expires

    db["licenses"][key] = entry
    _save_db(db)
    print(f"\n  Chave gerada: {key}")
    print(f"  Cliente: {owner}")
    if expires:
        print(f"  Expira: {expires}")
    print()
    return key


def cmd_listar() -> None:
    """Lista todas as licenças."""
    db = _load_db()
    lics = db.get("licenses", {})
    if not lics:
        print("Nenhuma licença cadastrada.")
        return

    print(f"\n{'CHAVE':<30} {'CLIENTE':<25} {'ATIVA':<8} {'EXPIRA':<12} {'MAQUINAS'}")
    print("-" * 100)
    for key, data in lics.items():
        active = "SIM" if data.get("active", True) else "NAO"
        expires = data.get("expires", "-")
        owner = data.get("owner", "?")
        machines = data.get("machines", [])
        maq = ", ".join(m[:12] for m in machines) if machines else "(livre)"
        print(f"{key:<30} {owner:<25} {active:<8} {expires:<12} {maq}")

    blocked = db.get("blocked_keys", [])
    if blocked:
        print(f"\nChaves bloqueadas: {', '.join(blocked)}")

    blocked_m = db.get("blocked_machines", [])
    if blocked_m:
        print(f"Máquinas bloqueadas: {', '.join(blocked_m)}")
    print()


def cmd_bloquear(key: str) -> None:
    """Bloqueia uma chave de licença."""
    db = _load_db()
    key = key.strip().upper()
    if key in db.get("licenses", {}):
        db["licenses"][key]["active"] = False
    if key not in db.get("blocked_keys", []):
        db.setdefault("blocked_keys", []).append(key)
    _save_db(db)
    print(f"[OK] Chave {key} bloqueada.")


def cmd_desbloquear(key: str) -> None:
    """Desbloqueia uma chave de licença."""
    db = _load_db()
    key = key.strip().upper()
    if key in db.get("licenses", {}):
        db["licenses"][key]["active"] = True
    blocked = db.get("blocked_keys", [])
    db["blocked_keys"] = [k for k in blocked if k.upper() != key]
    _save_db(db)
    print(f"[OK] Chave {key} desbloqueada.")


def cmd_bloquear_maquina(machine_id: str) -> None:
    """Bloqueia uma máquina pelo ID."""
    db = _load_db()
    if machine_id not in db.get("blocked_machines", []):
        db.setdefault("blocked_machines", []).append(machine_id)
    _save_db(db)
    print(f"[OK] Máquina {machine_id} bloqueada.")


def cmd_desvincular(key: str) -> None:
    """Remove todas as máquinas vinculadas a uma chave (permite reativação em outro PC)."""
    db = _load_db()
    key = key.strip().upper()
    if key in db.get("licenses", {}):
        machines = db["licenses"][key].get("machines", [])
        db["licenses"][key]["machines"] = []
        _save_db(db)
        if machines:
            print(f"[OK] Chave {key} desvinculada de {len(machines)} máquina(s).")
            print("     Lembre de executar 'exportar' e atualizar o gist.")
        else:
            print(f"Chave {key} já não tinha máquinas vinculadas.")
    else:
        print(f"Chave {key} não encontrada.")


def cmd_exportar() -> dict:
    """Exporta o JSON para ser usado no gist."""
    db = _load_db()
    out_file = Path(__file__).parent / "licenses.json"
    out_file.write_text(
        json.dumps(db, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[OK] Exportado para {out_file}")
    return db


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0].lower()

    if cmd == "gerar":
        if len(args) < 2:
            print("Uso: python license_manager.py gerar \"Nome do Cliente\" [--expires YYYY-MM-DD]")
            sys.exit(1)
        owner = args[1]
        expires = None
        if "--expires" in args:
            idx = args.index("--expires")
            if idx + 1 < len(args):
                expires = args[idx + 1]
        cmd_gerar(owner, expires)

    elif cmd == "listar":
        cmd_listar()

    elif cmd == "bloquear":
        if len(args) < 2:
            print("Uso: python license_manager.py bloquear FBOT-XXXX-XXXX-XXXX-XXXX")
            sys.exit(1)
        cmd_bloquear(args[1])

    elif cmd == "desbloquear":
        if len(args) < 2:
            print("Uso: python license_manager.py desbloquear FBOT-XXXX-XXXX-XXXX-XXXX")
            sys.exit(1)
        cmd_desbloquear(args[1])

    elif cmd == "bloquear-maquina":
        if len(args) < 2:
            print("Uso: python license_manager.py bloquear-maquina <machine_id>")
            sys.exit(1)
        cmd_bloquear_maquina(args[1])

    elif cmd == "desvincular":
        if len(args) < 2:
            print("Uso: python license_manager.py desvincular FBOT-XXXX-XXXX-XXXX-XXXX")
            sys.exit(1)
        cmd_desvincular(args[1])

    elif cmd == "exportar":
        cmd_exportar()

    else:
        print(f"Comando desconhecido: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
