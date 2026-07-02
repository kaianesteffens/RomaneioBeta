"""
Centralized Error Handler com padrão consistente.

Suporta:
- Logging estruturado com contexto (arquivo, função, linha)
- Retry automático com backoff exponencial
- Integração com error_reporter (opcional)
- Silent failures para casos onde falha é aceitável
- Type hints completos
"""
from __future__ import annotations

import inspect
import logging
import time
import traceback
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, Generic

try:
    import error_reporter
except ImportError:
    error_reporter = None  # type: ignore

try:
    from logging_conf import get_logger
except ImportError:
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)


T = TypeVar("T")

# Logger dedicado para error_handler
_logger = get_logger("error_handler")


class ErrorContext:
    """Contexto de um erro (arquivo, função, linha, etc)."""
    
    def __init__(
        self,
        message: str,
        exception: Exception | None = None,
        file: str | None = None,
        function: str | None = None,
        line: int | None = None,
    ):
        self.message = message
        self.exception = exception
        self.file = file or "<unknown>"
        self.function = function or "<unknown>"
        self.line = line or 0
        self.timestamp = time.time()
    
    def to_dict(self) -> dict[str, Any]:
        """Converte contexto para dicionário."""
        return {
            "message": self.message,
            "exception_type": type(self.exception).__name__ if self.exception else None,
            "exception_message": str(self.exception) if self.exception else None,
            "file": self.file,
            "function": self.function,
            "line": self.line,
            "timestamp": self.timestamp,
        }

    def to_log_extra(self, *, operation: str | None = None) -> dict[str, Any]:
        extra: dict[str, Any] = {
            "file": self.file,
            "function": self.function,
            "line": self.line,
        }
        if operation:
            extra["operation"] = operation
        return extra
    
    def __str__(self) -> str:
        loc = f"{self.file}:{self.line} in {self.function}()"
        if self.exception:
            return f"{self.message} | {type(self.exception).__name__}: {self.exception} | {loc}"
        return f"{self.message} | {loc}"


class ErrorHandler:
    """
    Handler centralizado de erros com padrão consistente.
    
    Exemplo de uso:
        try:
            result = some_operation()
        except Exception as e:
            ErrorHandler.handle_error(e, "Cotar frete TRD", retry_count=3)
        
        # Ou com retry automático:
        result = ErrorHandler.handle_with_recovery(
            some_operation,
            args=(),
            kwargs={},
            retry_count=3,
            backoff_factor=2.0,
        )
        
        # Ou para falhas silenciosas:
        result = ErrorHandler.silent_fail(
            some_operation,
            default="fallback_value",
        )
    """
    
    # Configuração global
    _report_to_server = True  # Se True, tenta enviar para error_reporter
    _log_level_default = logging.ERROR
    _backoff_factor_default = 2.0  # Exponential backoff: 1s, 2s, 4s, ...
    _backoff_base_default = 1.0  # delay = base * (factor ^ attempt)
    
    @classmethod
    def configure(
        cls,
        report_to_server: bool = True,
        log_level: int = logging.ERROR,
        backoff_factor: float = 2.0,
        backoff_base: float = 1.0,
    ) -> None:
        """Configura o ErrorHandler globalmente."""
        cls._report_to_server = report_to_server
        cls._log_level_default = log_level
        cls._backoff_factor_default = backoff_factor
        cls._backoff_base_default = backoff_base
    
    @staticmethod
    def _get_caller_context() -> tuple[str, str, int]:
        """Obtém contexto do chamador (arquivo, função, linha)."""
        try:
            current_file = Path(__file__).resolve()
            for frame_info in inspect.stack()[1:]:
                filename = Path(frame_info.filename).resolve()
                module_name = str(frame_info.frame.f_globals.get("__name__", ""))
                if filename == current_file:
                    continue
                if module_name.startswith("_pytest.") or ("site-packages" in str(filename) and "pytest" in str(filename)):
                    continue
                return filename.name, frame_info.function, frame_info.lineno
        except Exception:
            pass
        return "<unknown>", "<unknown>", 0
    
    @classmethod
    def log_error(
        cls,
        message: str,
        level: int = logging.ERROR,
        exception: Exception | None = None,
        caller_context: tuple[str, str, int] | None = None,
    ) -> ErrorContext:
        """
        Loga um erro com contexto estruturado.
        
        Args:
            message: Mensagem descritiva do erro
            level: Nível de logging (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            exception: Exceção capturada (opcional)
        
        Returns:
            ErrorContext com o contexto do erro
        """
        file, function, line = caller_context or cls._get_caller_context()
        ctx = ErrorContext(
            message=message,
            exception=exception,
            file=file,
            function=function,
            line=line,
        )
        
        # Log estruturado
        if exception:
            _logger.log(
                level,
                "%s | %s: %s",
                message,
                type(exception).__name__,
                exception,
                exc_info=exception,
                extra=ctx.to_log_extra(operation="log_error"),
            )
        else:
            _logger.log(level, message, extra=ctx.to_log_extra(operation="log_error"))
        
        return ctx
    
    @classmethod
    def report_to_server(cls, error_data: dict[str, Any]) -> bool:
        """
        Registra erro no log local (via error_reporter).

        O envio ao servidor foi removido: ``error_reporter.report_error_message``
        agora apenas grava no log local sanitizado. O nome é mantido por
        compatibilidade com os chamadores existentes.

        Args:
            error_data: Dicionário com dados do erro

        Returns:
            True se registrado, False caso contrário
        """
        if not cls._report_to_server or error_reporter is None:
            return False

        try:
            context = error_data.get("context", "error_handler")
            message = error_data.get("message", "Unknown error")

            # Registra via error_reporter (log local)
            if hasattr(error_reporter, "report_error_message"):
                error_reporter.report_error_message(
                    message=f"{context}: {message}",
                    context=context,
                    wait=False,  # Não bloqueia
                )
                return True
        except Exception as e:
            _logger.debug(
                "Falha ao registrar erro no log local: %s",
                e,
                extra={
                    "operation": "report_to_server",
                    "file": error_data.get("file"),
                    "function": error_data.get("function"),
                    "line": error_data.get("line"),
                },
            )
            return False
        
        return False
    
    @classmethod
    def handle_error(
        cls,
        exception: Exception,
        context: str = "",
        retry_count: int = 3,
        should_report: bool = True,
    ) -> bool:
        """
        Trata um erro com logging, retry optional e report opcional.
        
        Args:
            exception: Exceção capturada
            context: Descrição do contexto onde o erro ocorreu (ex: "Cotar frete TRD")
            retry_count: Número de retries (0 = sem retry)
            should_report: Se True, tenta enviar para servidor
        
        Returns:
            True se erro foi tratado, False caso contrário
        """
        caller_context = cls._get_caller_context()
        file, function, line = caller_context
        
        # Loga o erro
        error_ctx = cls.log_error(
            message=context or "Error occurred",
            level=cls._log_level_default,
            exception=exception,
            caller_context=caller_context,
        )
        
        # Prepara dados para envio ao servidor
        error_data = {
            "context": context,
            "message": str(exception),
            "exception_type": type(exception).__name__,
            "file": file,
            "function": function,
            "line": line,
            "retry_count": retry_count,
        }
        
        # Tenta enviar para servidor
        if should_report:
            cls.report_to_server(error_data)
        
        return True
    
    @classmethod
    def handle_with_recovery(
        cls,
        func: Callable[..., T],
        *args,
        retry_count: int = 3,
        backoff_factor: Optional[float] = None,
        backoff_base: Optional[float] = None,
        context: str = "",
        **kwargs,
    ) -> T | None:
        """
        Executa função com retry automático e backoff exponencial.
        
        Args:
            func: Função a executar
            *args: Argumentos posicionais para func
            retry_count: Número máximo de tentativas (padrão: 3)
            backoff_factor: Fator multiplicador para backoff exponencial
            backoff_base: Delay base em segundos
            context: Descrição do contexto
            **kwargs: Argumentos nomeados para func
        
        Returns:
            Resultado de func se sucesso, None se todas as tentativas falharem
        
        Exemplo:
            result = ErrorHandler.handle_with_recovery(
                risky_function,
                arg1, arg2,
                retry_count=5,
                context="Conectar ao banco de dados",
            )
        """
        if backoff_factor is None:
            backoff_factor = cls._backoff_factor_default
        if backoff_base is None:
            backoff_base = cls._backoff_base_default
        
        func_name = getattr(func, "__name__", str(func))
        context_str = context or func_name
        
        for attempt in range(1, retry_count + 1):
            try:
                _logger.debug(f"[{context_str}] Tentativa {attempt}/{retry_count}")
                return func(*args, **kwargs)
            except Exception as e:
                is_last = attempt == retry_count
                
                if is_last:
                    # Última tentativa falhou
                    cls.handle_error(
                        e,
                        context=f"{context_str} (última tentativa: {attempt}/{retry_count})",
                        retry_count=retry_count,
                    )
                    return None
                else:
                    # Calcula delay com backoff exponencial
                    delay = backoff_base * (backoff_factor ** (attempt - 1))
                    _logger.warning(
                        "[%s] Tentativa %s/%s falhou: %s. Retry em %.1fs...",
                        context_str,
                        attempt,
                        retry_count,
                        e,
                        delay,
                        extra={"operation": "handle_with_recovery"},
                    )
                    time.sleep(delay)
        
        return None
    
    @classmethod
    def silent_fail(
        cls,
        func: Callable[..., T],
        *args,
        default: T | None = None,
        context: str = "",
        log_on_fail: bool = True,
        **kwargs,
    ) -> T | None:
        """
        Executa função com falha silenciosa (retorna default em caso de erro).
        
        Útil para operações onde falha é aceitável e não deve quebrar a aplicação.
        
        Args:
            func: Função a executar
            *args: Argumentos posicionais para func
            default: Valor padrão se func falhar
            context: Descrição do contexto (para logging)
            log_on_fail: Se True, loga o erro (em nível DEBUG)
            **kwargs: Argumentos nomeados para func
        
        Returns:
            Resultado de func se sucesso, default se falhar
        
        Exemplo:
            # Tenta ler arquivo, retorna None se falhar
            content = ErrorHandler.silent_fail(
                open,
                "config.txt",
                default=None,
                context="Ler arquivo de configuração",
            )
        """
        func_name = getattr(func, "__name__", str(func))
        context_str = context or func_name
        
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if log_on_fail:
                _logger.debug(
                    "[%s] Falha silenciosa (retornando default): %s: %s",
                    context_str,
                    type(e).__name__,
                    e,
                    extra={"operation": "silent_fail"},
                )
            return default
    
    @classmethod
    def as_decorator(
        cls,
        retry_count: int = 3,
        backoff_factor: Optional[float] = None,
        context: str = "",
    ):
        """
        Decorator para aplicar retry automático em função.
        
        Args:
            retry_count: Número máximo de tentativas
            backoff_factor: Fator multiplicador para backoff exponencial
            context: Descrição do contexto
        
        Exemplo:
            @ErrorHandler.as_decorator(retry_count=5, context="API Call")
            def fetch_data():
                return requests.get(url).json()
            
            result = fetch_data()  # Retry automático se falhar
        """
        def decorator(func: Callable[..., T]) -> Callable[..., T | None]:
            @wraps(func)
            def wrapper(*args, **kwargs) -> T | None:
                ctx = context or getattr(func, "__name__", "decorated_function")
                return cls.handle_with_recovery(
                    func,
                    *args,
                    retry_count=retry_count,
                    backoff_factor=backoff_factor,
                    context=ctx,
                    **kwargs,
                )
            return wrapper
        return decorator
    
    @classmethod
    def as_silent_decorator(
        cls,
        default: Any = None,
        context: str = "",
        log_on_fail: bool = True,
    ):
        """
        Decorator para aplicar silent_fail em função.
        
        Args:
            default: Valor padrão se função falhar
            context: Descrição do contexto
            log_on_fail: Se True, loga o erro em nível DEBUG
        
        Exemplo:
            @ErrorHandler.as_silent_decorator(default={}, context="Parse JSON")
            def parse_json(text):
                return json.loads(text)
            
            data = parse_json(invalid_text)  # Retorna {} se falhar
        """
        def decorator(func: Callable[..., T]) -> Callable[..., T | None]:
            @wraps(func)
            def wrapper(*args, **kwargs) -> T | None:
                ctx = context or getattr(func, "__name__", "decorated_function")
                return cls.silent_fail(
                    func,
                    *args,
                    default=default,
                    context=ctx,
                    log_on_fail=log_on_fail,
                    **kwargs,
                )
            return wrapper
        return decorator


# Funções de conveniência (aliases para uso direto)
def handle_error(
    exception: Exception,
    context: str = "",
    retry_count: int = 3,
) -> bool:
    """Alias para ErrorHandler.handle_error()."""
    return ErrorHandler.handle_error(exception, context, retry_count)


def handle_with_recovery(
    func: Callable[..., T],
    *args,
    retry_count: int = 3,
    context: str = "",
    **kwargs,
) -> T | None:
    """Alias para ErrorHandler.handle_with_recovery()."""
    return ErrorHandler.handle_with_recovery(
        func, *args, retry_count=retry_count, context=context, **kwargs
    )


def silent_fail(
    func: Callable[..., T],
    *args,
    default: T | None = None,
    context: str = "",
    **kwargs,
) -> T | None:
    """Alias para ErrorHandler.silent_fail()."""
    return ErrorHandler.silent_fail(func, *args, default=default, context=context, **kwargs)


def retry_on_error(
    retry_count: int = 3,
    context: str = "",
):
    """Decorator para retry automático."""
    return ErrorHandler.as_decorator(retry_count=retry_count, context=context)


def silent_on_error(
    default: Any = None,
    context: str = "",
):
    """Decorator para silent_fail automático."""
    return ErrorHandler.as_silent_decorator(default=default, context=context)


__all__ = [
    "ErrorHandler",
    "ErrorContext",
    "handle_error",
    "handle_with_recovery",
    "silent_fail",
    "retry_on_error",
    "silent_on_error",
]
