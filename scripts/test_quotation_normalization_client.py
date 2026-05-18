from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from quotation_normalization_client import normalize_quotation_remote_shadow


def main() -> int:
    payload = {
        "modo": "manual_script",
        "cep_destino": "90010-123",
        "uf_destino": "RS",
        "volumes": 2,
        "peso_total_kg": 3.3,
        "valor_nf": 150.99,
        "cubagem_m3": 0.044,
        "medidas": [
            {
                "quantidade": 2,
                "comprimento_cm": 45,
                "largura_cm": 31,
                "altura_cm": 31,
            }
        ],
    }
    result = normalize_quotation_remote_shadow("manual", payload=payload, wait=True)
    sent = bool(result.get("sent"))
    print(f"sent: {'true' if sent else 'false'}")
    if result.get("skipped"):
        print("skipped: true")
    status_code = result.get("status_code")
    if status_code is not None:
        print(f"status_code: {status_code}")
    comparison = result.get("comparison")
    if isinstance(comparison, dict):
        print(f"matched: {'true' if comparison.get('matched') else 'false'}")
        if comparison.get("differences"):
            print(f"differences: {comparison.get('differences')}")
        if comparison.get("remote_warnings"):
            print(f"warnings: {comparison.get('remote_warnings')}")
    return 0 if sent and isinstance(comparison, dict) and comparison.get("matched") else 1


if __name__ == "__main__":
    raise SystemExit(main())
