"""Utilidades compartilhadas entre providers."""
import re
from typing import Optional, Any
from decimal import Decimal, ROUND_HALF_UP


def _digits(value: str) -> str:
    """Extrai apenas dígitos de uma string.
    
    Args:
        value: String de entrada, pode conter não-dígitos
        
    Returns:
        String contendo apenas dígitos, ou string vazia se nenhum dígito
    """
    return re.sub(r"\D", "", str(value or ""))


def _fmt_decimal(
    value: float,
    decimals: int = 2,
    comma: bool = True
) -> str:
    """Formata decimal com separador brasileiro (vírgula) ou ponto.
    
    Args:
        value: Valor numérico a formatar
        decimals: Número de casas decimais (padrão 2)
        comma: Se True, usa vírgula; se False, usa ponto (padrão True)
        
    Returns:
        String formatada com valor decimal
    """
    txt = f"{float(value):.{decimals}f}"
    return txt.replace(".", ",") if comma else txt


def _fmt_peso(value: float, decimals: int = 3) -> str:
    """Formata peso com separador decimal brasileiro.
    
    Args:
        value: Valor de peso (em kg ou unidade apropriada)
        decimals: Número de casas decimais (padrão 3)
        
    Returns:
        String formatada com vírgula como separador decimal
    """
    return f"{float(value):.{decimals}f}".replace(".", ",")


def _parse_decimal_any(raw: Any) -> Optional[float]:
    """Converte qualquer valor para float, interpretando múltiplos formatos.
    
    Suporta formatos:
    - "1.234,56" (br) → 1234.56
    - "1,234.56" (en) → 1234.56
    - "1234,56" (br simples) → 1234.56
    - "1234.56" (en simples) → 1234.56
    - Valor numérico direto
    
    Args:
        raw: Valor a converter (string, int, float, etc)
        
    Returns:
        Float convertido ou None se não conseguir converter
    """
    txt = re.sub(r"[^\d,.\-]", "", str(raw or "").strip())
    if not txt:
        return None
    if "," in txt and "." in txt:
        # Identifica separador decimal (o último entre vírgula/ponto)
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
    elif "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


def _parse_int_any(raw: Any) -> int:
    """Extrai primeiro número inteiro de qualquer valor.
    
    Args:
        raw: Valor de entrada (string, int, etc)
        
    Returns:
        Primeiro número inteiro encontrado, ou 0 se não encontrar
    """
    m = re.search(r"\d+", str(raw or ""))
    return int(m.group(0)) if m else 0


def _parse_brl(valor: str) -> float:
    """Converte string em formato BRL para float.
    
    Suporta:
    - "1.234,56" → 1234.56
    - "R$ 1.234,56" → 1234.56
    
    Args:
        valor: String em formato BRL
        
    Returns:
        Float convertido
    """
    normalized = re.sub(r"[^\d,.\-]", "", str(valor or ""))
    if not normalized:
        return 0.0
    return float(normalized.replace(".", "").replace(",", "."))


def _format_decimal_br_2(valor: float, *, min_value: Optional[float] = None) -> str:
    """Formata decimal para formato BR com exatamente 2 casas decimais.
    
    Usa arredondamento ROUND_HALF_UP para consistência.
    
    Args:
        valor: Valor a formatar
        min_value: Valor mínimo (opcional)
        
    Returns:
        String formatada "X.XXX,XX" com vírgula como separador
    """
    dec = Decimal(str(valor))
    if min_value is not None:
        dec = max(dec, Decimal(str(min_value)))
    dec = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{dec:.2f}".replace(".", ",")


def _format_currency(valor: float) -> str:
    """Formata valor em formato de moeda brasileira (R$).
    
    Exemplo: 1234.56 → "R$ 1.234,56"
    
    Args:
        valor: Valor a formatar
        
    Returns:
        String com formato de moeda brasileiro
    """
    dec = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    inteiro = int(dec)
    centavos = int((dec - inteiro) * 100)
    return f"R$ {inteiro:,}".replace(",", ".") + f",{centavos:02d}"


def _format_cnpj(digits: str) -> str:
    """Formata string de dígitos para padrão CNPJ (XX.XXX.XXX/XXXX-XX).
    
    Args:
        digits: String contendo apenas dígitos (14 caracteres esperados)
        
    Returns:
        String formatada como CNPJ
    """
    d = _digits(digits)
    if len(d) != 14:
        return d
    return f"{d[0:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"


def _format_cpf(digits: str) -> str:
    """Formata string de dígitos para padrão CPF (XXX.XXX.XXX-XX).
    
    Args:
        digits: String contendo apenas dígitos (11 caracteres esperados)
        
    Returns:
        String formatada como CPF
    """
    d = _digits(digits)
    if len(d) != 11:
        return d
    return f"{d[0:3]}.{d[3:6]}.{d[6:9]}-{d[9:11]}"


# ─── Stealth JavaScript para contornar detecção de automação ───────────────────

STEALTH_JS = """
// 1. navigator.webdriver = undefined (sinal padrão de Selenium/Playwright)
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. Simula plugins reais (Chrome sem plugins é sinal de bot)
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format',
              0: { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' }, length: 1 },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '',
              0: { type: 'application/pdf', suffixes: 'pdf', description: '' }, length: 1 },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '',
              0: { type: 'application/x-nacl', suffixes: '', description: 'Native Client Executable' },
              1: { type: 'application/x-pnacl', suffixes: '', description: 'Portable Native Client Executable' }, length: 2 },
        ];
        arr.refresh = () => {};
        return arr;
    }
});

// 3. Simula mimeTypes
Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
        const arr = [
            { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: {} },
            { type: 'application/pdf', suffixes: 'pdf', description: '', enabledPlugin: {} },
        ];
        return arr;
    }
});

// 4. Languages coerente com locale pt-BR
Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR', 'pt', 'en-US', 'en'] });

// 5. chrome.runtime (existe em Chrome real, ausente em Playwright)
if (!window.chrome) window.chrome = {};
if (!window.chrome.runtime) window.chrome.runtime = { id: undefined };
// chrome.app / chrome.csi para parecer Chrome real
if (!window.chrome.app) {
    window.chrome.app = {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
        getDetails: () => null,
        getIsInstalled: () => false,
        installState: () => 'not_installed',
        runningState: () => 'cannot_run',
    };
}

// 6. Permissions.query — evita leak de "denied" para notification
const origQuery = window.navigator.permissions?.query?.bind(window.navigator.permissions);
if (origQuery) {
    window.navigator.permissions.query = (params) => {
        if (params.name === 'notifications') {
            return Promise.resolve({ state: Notification.permission });
        }
        return origQuery(params);
    };
}

// 7. Esconde detecção de CDP (Chrome DevTools Protocol)
// reCAPTCHA verifica window.cdc_adoQpoasnfa76pfcZLmcfl_* props
(function() {
    const props = Object.getOwnPropertyNames(window).filter(p => /^cdc_/.test(p));
    for (const p of props) { delete window[p]; }
})();

// 8. Falsifica connection.rtt (IP fingerprint via RTT)
if (navigator.connection) {
    Object.defineProperty(navigator.connection, 'rtt', { get: () => 50 });
}

// 9. Garante que screen dimensions são coerentes
Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });

// 10. navigator.hardwareConcurrency (automação frequentemente reporta 0 ou 2)
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

// 11. navigator.deviceMemory (ausente em automação)
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

// 12. WebGL vendor/renderer (evita fingerprint de "SwiftShader" que denuncia headless)
(function() {
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
        if (param === 37445) return 'Google Inc. (NVIDIA)';    // UNMASKED_VENDOR_WEBGL
        if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650, OpenGL 4.5)'; // UNMASKED_RENDERER_WEBGL
        return getParam.call(this, param);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
        const getParam2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return 'Google Inc. (NVIDIA)';
            if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650, OpenGL 4.5)';
            return getParam2.call(this, param);
        };
    }
})();

// 13. Falsifica Notification.permission (evita sinal de "default" em automação)
try {
    Object.defineProperty(Notification, 'permission', { get: () => 'default' });
} catch(e) {}

// 14. Remove sourceURL/sourceMapping headers que indicam Playwright
(function() {
    const origEval = window.eval;
    window.eval = function() {
        try { return origEval.apply(this, arguments); }
        catch(e) { throw e; }
    };
    window.eval.toString = () => 'function eval() { [native code] }';
})();
"""


def get_stealth_script(*, preserve_eval: bool = True) -> str:
    """Retorna o script de stealth JS completo para injecao no browser."""
    if preserve_eval:
        return STEALTH_JS
    marker = """// 14. Remove sourceURL/sourceMapping headers que indicam Playwright
(function() {
    const origEval = window.eval;
    window.eval = function() {
        try { return origEval.apply(this, arguments); }
        catch(e) { throw e; }
    };
    window.eval.toString = () => 'function eval() { [native code] }';
})();
"""
    return STEALTH_JS.replace(marker, "")
