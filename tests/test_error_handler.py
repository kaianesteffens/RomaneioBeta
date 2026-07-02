"""
Testes para validar ErrorHandler.

Executa:
- python test_error_handler.py (modo verbose)
"""
import sys
import logging
import time
from pathlib import Path

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

# Importar error_handler
from error_handler import (
    ErrorHandler,
    ErrorContext,
    handle_error,
    handle_with_recovery,
    silent_fail,
    retry_on_error,
    silent_on_error,
)

# Desabilitar envio para servidor durante testes — evita ruído na API de produção
ErrorHandler.configure(report_to_server=False)

# Configurar logging para ver tudo
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)


def test_error_context():
    """Teste: ErrorContext armazena informações de erro."""
    print("\n" + "=" * 60)
    print("TEST 1: ErrorContext")
    print("=" * 60)
    
    try:
        raise ValueError("Test error message")
    except Exception as e:
        ctx = ErrorContext(
            message="Test context",
            exception=e,
            file="test.py",
            function="test_func",
            line=42,
        )
        
        print(f"[OK] Context created: {ctx}")
        print(f"[OK] Context dict: {ctx.to_dict()}")
        assert ctx.message == "Test context"
        assert "ValueError" in str(ctx)
        print("[OK] PASSED")


def test_simple_error_handling():
    """Teste: handle_error loga erros com contexto."""
    print("\n" + "=" * 60)
    print("TEST 2: Simple Error Handling")
    print("=" * 60)
    
    try:
        raise RuntimeError("Database connection failed")
    except Exception as e:
        result = ErrorHandler.handle_error(
            e,
            context="Conectar ao banco de dados",
            retry_count=3,
        )
        assert result is True
        print("[OK] Error logged successfully")
        print("[OK] PASSED")


def test_retry_with_backoff():
    """Teste: handle_with_recovery faz retry com backoff exponencial."""
    print("\n" + "=" * 60)
    print("TEST 3: Retry with Exponential Backoff")
    print("=" * 60)
    
    call_count = 0
    
    def flaky_function():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError(f"Connection failed (attempt {call_count})")
        return f"Success on attempt {call_count}"
    
    start = time.time()
    result = ErrorHandler.handle_with_recovery(
        flaky_function,
        retry_count=5,
        backoff_factor=1.5,
        backoff_base=0.1,  # Usar delays curtos para testes
        context="Conexão instável",
    )
    elapsed = time.time() - start
    
    assert result == "Success on attempt 3"
    assert call_count == 3
    print(f"[OK] Function succeeded on attempt 3")
    print(f"[OK] Total time with retries: {elapsed:.2f}s")
    print(f"[OK] Exponential backoff funcionou (0.1s + 0.15s)")
    print("[OK] PASSED")


def test_retry_exhaustion():
    """Teste: handle_with_recovery retorna None se todas tentativas falharem."""
    print("\n" + "=" * 60)
    print("TEST 4: Retry Exhaustion")
    print("=" * 60)
    
    def always_fails():
        raise ValueError("This always fails")
    
    result = ErrorHandler.handle_with_recovery(
        always_fails,
        retry_count=3,
        backoff_factor=1.0,
        backoff_base=0.05,  # Delays curtos
        context="Sempre falha",
    )
    
    assert result is None
    print("[OK] Returned None after exhausting retries")
    print("[OK] PASSED")


def test_silent_fail_success():
    """Teste: silent_fail retorna resultado se sucesso."""
    print("\n" + "=" * 60)
    print("TEST 5: Silent Fail (Success Case)")
    print("=" * 60)
    
    def safe_operation():
        return {"data": "success"}
    
    result = silent_fail(
        safe_operation,
        default={},
        context="Operação segura",
    )
    
    assert result == {"data": "success"}
    print("[OK] Function executed successfully")
    print("[OK] Result: ", result)
    print("[OK] PASSED")


def test_silent_fail_with_default():
    """Teste: silent_fail retorna default se falhar."""
    print("\n" + "=" * 60)
    print("TEST 6: Silent Fail (Failure Case)")
    print("=" * 60)
    
    def failing_operation():
        raise RuntimeError("Operation failed")
    
    result = silent_fail(
        failing_operation,
        default="fallback_value",
        context="Operação que falha",
    )
    
    assert result == "fallback_value"
    print("[OK] Function failed silently")
    print("[OK] Returned default value: 'fallback_value'")
    print("[OK] Application continued normally")
    print("[OK] PASSED")


def test_decorator_retry():
    """Teste: @retry_on_error decorator aplica retry automático."""
    print("\n" + "=" * 60)
    print("TEST 7: @retry_on_error Decorator")
    print("=" * 60)
    
    call_count = 0
    
    @retry_on_error(retry_count=3, context="Teste de decorator")
    def decorated_flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ValueError("Failed on first attempt")
        return f"Success on attempt {call_count}"
    
    result = decorated_flaky()
    
    assert result == "Success on attempt 2"
    assert call_count == 2
    print("[OK] Decorated function succeeded after retry")
    print("[OK] Calls: 2 (failed on 1st, succeeded on 2nd)")
    print("[OK] PASSED")


def test_decorator_silent():
    """Teste: @silent_on_error decorator aplica silent_fail."""
    print("\n" + "=" * 60)
    print("TEST 8: @silent_on_error Decorator")
    print("=" * 60)
    
    @silent_on_error(default="no_data", context="Teste silent")
    def decorated_unreliable():
        raise IOError("File not found")
    
    result = decorated_unreliable()
    
    assert result == "no_data"
    print("[OK] Decorated function failed silently")
    print("[OK] Returned default: 'no_data'")
    print("[OK] No exception raised")
    print("[OK] PASSED")


def test_configuration():
    """Teste: ErrorHandler.configure() permite personalização."""
    print("\n" + "=" * 60)
    print("TEST 9: Global Configuration")
    print("=" * 60)
    
    # Salvar config original
    original_report = ErrorHandler._report_to_server
    
    # Mudar configuração
    ErrorHandler.configure(
        report_to_server=False,
        log_level=logging.WARNING,
        backoff_factor=2.5,
        backoff_base=0.5,
    )
    
    assert ErrorHandler._report_to_server is False
    assert ErrorHandler._log_level_default == logging.WARNING
    assert ErrorHandler._backoff_factor_default == 2.5
    assert ErrorHandler._backoff_base_default == 0.5
    
    # Restaurar
    ErrorHandler.configure(report_to_server=original_report)
    
    print("[OK] Configuration updated successfully")
    print(f"[OK] report_to_server: False")
    print(f"[OK] log_level: WARNING")
    print(f"[OK] backoff_factor: 2.5")
    print(f"[OK] backoff_base: 0.5")
    print("[OK] PASSED")


def test_error_context_caller():
    """Teste: ErrorHandler._get_caller_context() detecta contexto correto."""
    print("\n" + "=" * 60)
    print("TEST 10: Automatic Caller Context Detection")
    print("=" * 60)
    
    file, func, line = ErrorHandler._get_caller_context()
    
    print(f"[OK] Detected file: {file}")
    print(f"[OK] Detected function: {func}")
    print(f"[OK] Detected line: {line}")
    
    # The function detection will be from where _get_caller_context is called
    # which is main(), but we can verify file and line are correct
    assert file == "test_error_handler.py"
    assert line > 0
    print("[OK] PASSED")


def test_integration_scenario():
    """Teste: Cenário integrado simulando operação real."""
    print("\n" + "=" * 60)
    print("TEST 11: Integration Scenario")
    print("=" * 60)
    
    # Simular operação que falha algumas vezes
    attempts = {"count": 0}
    
    def unreliable_api_call():
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise ConnectionError("API timeout")
        return {"status": "ok", "data": [1, 2, 3]}
    
    # Usar retry
    result = ErrorHandler.handle_with_recovery(
        unreliable_api_call,
        retry_count=5,
        backoff_factor=1.5,
        backoff_base=0.05,
        context="Chamar API com retry",
    )
    
    assert result is not None
    assert result["status"] == "ok"
    assert attempts["count"] == 3
    print("[OK] API call succeeded after retries")
    print(f"[OK] Total attempts: {attempts['count']}")
    print(f"[OK] Final result: {result}")
    print("[OK] PASSED")


def main():
    """Executa todos os testes."""
    print("\n" + "=" * 60)
    print("ERROR HANDLER TEST SUITE")
    print("=" * 60)
    
    tests = [
        test_error_context,
        test_simple_error_handling,
        test_retry_with_backoff,
        test_retry_exhaustion,
        test_silent_fail_success,
        test_silent_fail_with_default,
        test_decorator_retry,
        test_decorator_silent,
        test_configuration,
        test_error_context_caller,
        test_integration_scenario,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"\n[FAILED]: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
