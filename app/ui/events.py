from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent


class UpdateResultEvent(QEvent):
    """Evento para atualizar o resultado na UI."""

    EventType = QEvent.Type(QEvent.registerEventType())

    def __init__(self, result: str):
        super().__init__(self.EventType)
        self.result = result


class UpdateFinishedEvent(QEvent):
    """Evento para indicar que a cotação terminou."""

    EventType = QEvent.Type(QEvent.registerEventType())

    def __init__(self):
        super().__init__(self.EventType)


class StatusUpdateEvent(QEvent):
    """Evento para atualizar o status na UI."""

    EventType = QEvent.Type(QEvent.registerEventType())

    def __init__(self, msg: str):
        super().__init__(self.EventType)
        self.msg = msg


class CotacaoProgressEvent(QEvent):
    """Evento para atualizar progresso de cotações em tempo real."""

    EventType = QEvent.Type(QEvent.registerEventType())

    def __init__(self, payload: dict[str, Any]):
        super().__init__(self.EventType)
        self.payload = payload or {}


class LoginStatusEvent(QEvent):
    """Evento para atualizar status de login individual de transportadora."""

    EventType = QEvent.Type(QEvent.registerEventType())

    def __init__(self, nome: str, status: str):
        super().__init__(self.EventType)
        self.nome = nome
        self.status = status


class RastreioResultEvent(QEvent):
    """Evento para atualizar resultado de rastreamento na UI."""

    EventType = QEvent.Type(QEvent.registerEventType())

    def __init__(self, indice: int, total: int, resultado: Any):
        super().__init__(self.EventType)
        self.indice = indice
        self.total = total
        self.resultado = resultado


class RastreioFinishedEvent(QEvent):
    """Evento para indicar que o rastreamento terminou."""

    EventType = QEvent.Type(QEvent.registerEventType())

    def __init__(self, resultados: list[Any]):
        super().__init__(self.EventType)
        self.resultados = resultados
