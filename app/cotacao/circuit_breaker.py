"""Circuit breaker por provider para bloquear providers com falhas consecutivas."""

from __future__ import annotations

import time
from dataclasses import dataclass


_FAILURE_THRESHOLD = 3       # falhas consecutivas para abrir o circuito
_RECOVERY_TIMEOUT_S = 300.0  # 5 minutos antes de tentar novamente (half-open)


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


class ProviderCircuitBreaker:
    """Rastreia falhas consecutivas por provider e bloqueia temporariamente
    providers que excedem o limite de falhas.

    Estados:
      - closed  : normal, provider disponível
      - open    : bloqueado após ``_FAILURE_THRESHOLD`` falhas consecutivas
      - half-open : após ``_RECOVERY_TIMEOUT_S`` segundos, permite uma tentativa
    """

    def __init__(
        self,
        failure_threshold: int = _FAILURE_THRESHOLD,
        recovery_timeout_s: float = _RECOVERY_TIMEOUT_S,
    ) -> None:
        self._threshold = failure_threshold
        self._timeout = recovery_timeout_s
        self._states: dict[str, _CircuitState] = {}

    def _state(self, nome: str) -> _CircuitState:
        key = str(nome).strip().lower()
        if key not in self._states:
            self._states[key] = _CircuitState()
        return self._states[key]

    def is_open(self, nome: str) -> bool:
        """Retorna True se o circuito está aberto (provider bloqueado).

        Em half-open (timeout expirado) retorna False para permitir uma tentativa.
        """
        state = self._state(nome)
        if state.opened_at is None:
            return False
        elapsed = time.monotonic() - state.opened_at
        if elapsed >= self._timeout:
            # Permite tentativa half-open sem resetar ainda
            return False
        return True

    def record_failure(self, nome: str) -> None:
        """Registra uma falha; abre o circuito se o limite for atingido."""
        state = self._state(nome)
        state.failures += 1
        if state.failures >= self._threshold:
            state.opened_at = time.monotonic()

    def record_success(self, nome: str) -> None:
        """Registra sucesso e fecha o circuito (reset completo)."""
        state = self._state(nome)
        state.failures = 0
        state.opened_at = None


__all__ = ["ProviderCircuitBreaker"]
