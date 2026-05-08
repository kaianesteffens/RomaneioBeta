#!/usr/bin/env python3
"""Validate Fretio update ZIP artifacts before release."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path, PurePosixPath


APP_EXE_NAMES = {"fretio.exe", "fretebot.exe"}


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"caminho inseguro no ZIP: {name!r}")
    if path.parts and ":" in path.parts[0]:
        raise ValueError(f"caminho absoluto no ZIP: {name!r}")
    return normalized


def _strip_single_root(names: list[str]) -> tuple[list[str], str | None]:
    files = [name for name in names if name and not name.endswith("/")]
    if not files:
        return names, None
    parts = [name.split("/") for name in files]
    first_parts = {item[0] for item in parts if item}
    has_root_file = any(len(item) == 1 for item in parts)
    if len(first_parts) == 1 and not has_root_file:
        root = next(iter(first_parts))
        return [
            name[len(root) + 1:] if name == root or name.startswith(root + "/") else name
            for name in names
        ], root
    return names, None


def validate(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = [_safe_name(info.filename) for info in zf.infolist()]

    stripped, root = _strip_single_root(names)
    files = {name.rstrip("/").lower() for name in stripped if name and not name.endswith("/")}
    dirs = {name.rstrip("/").lower() for name in stripped if name.endswith("/")}
    dirs.update(
        "/".join(file_name.split("/")[:-1])
        for file_name in files
        if "/" in file_name
    )

    missing: list[str] = []
    if not (APP_EXE_NAMES & files):
        missing.append("Fretio.exe ou FreteBot.exe na raiz")
    if "version.txt" not in files and "_internal/version.txt" not in files:
        missing.append("version.txt ou _internal/version.txt")
    if "_internal" not in dirs:
        missing.append("_internal")

    if missing:
        raise ValueError("ZIP de update inválido: faltando " + ", ".join(missing))

    if root:
        print(
            f"[AVISO] ZIP contém pasta raiz única {root!r}; "
            "launcher/updater suportam, mas o formato preferido é conteúdo na raiz."
        )
    print(f"[OK] ZIP de update válido: {zip_path}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Uso: validate_update_zip.py caminho\\Fretio-Update-X.Y.zip", file=sys.stderr)
        return 2
    zip_path = Path(argv[1])
    try:
        validate(zip_path)
    except Exception as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
