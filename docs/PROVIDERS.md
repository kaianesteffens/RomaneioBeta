# Providers de Transportadoras

Os providers ficam em `app/fretio/src/fretio/providers`. Cada provider encapsula uma transportadora e pode usar Playwright localmente para login, preenchimento e leitura de resultado.

## Providers atuais

Registrados em `app/fretio/src/fretio/providers/factory.py`:

- `braspress`
- `bauer`
- `trd`
- `agex`
- `eucatur`
- `rodonaves`
- `alfa`
- `coopex`
- `translovato`

Cada provider lê apenas sua seção em `CONFIG.toml`, por exemplo:

```toml
[transportadoras.exemplo]
habilitado = true
usuario = "usuario@example.com"
senha = "SENHA_LOCAL"
headless = true
ufs_atendidas = ["SP", "PR"]
```

## Contrato de cotação

Novos providers devem usar `fretio.quotation_contract.QuoteRequest` e `QuoteResponse`.

Exemplo de entrada:

```python
from fretio.quotation_contract import QuoteRequest

request = QuoteRequest(
    origem_cep="01001000",
    destino_cep="80010000",
    uf_destino="PR",
    cnpj_destinatario="",
    peso_total_kg=12.5,
    valor_nf=950.0,
    volumes=2,
    cubagem_m3=0.08,
    cubagens=[
        {"quantidade": 2, "comprimento_cm": 40, "largura_cm": 30, "altura_cm": 20}
    ],
    tipo_frete="CIF",
    metadata={"source_type": "manual"},
)
```

Exemplo de resposta:

```python
from fretio.quotation_contract import QuoteResponse

return QuoteResponse.ok(
    provider="exemplo",
    valor_frete=123.45,
    prazo_dias=4,
    detalhes="Cotação retornada pelo portal",
    stage="resultado",
)
```

Status permitidos:

- `ok`
- `sem_cotacao`
- `erro`
- `desabilitada`
- `nao_atendido`

Use os helpers `QuoteResponse.ok(...)`, `QuoteResponse.error(...)`, `QuoteResponse.no_quote(...)` e `QuoteResponse.disabled(...)`. O campo `raw` é sanitizado, mas ainda assim não inclua credenciais, HTML bruto ou documentos completos.

## Como criar nova transportadora

1. Criar `app/fretio/src/fretio/providers/nova.py`.
2. Implementar uma classe que herde de `ProviderBase`.
3. Aceitar no `__init__` somente os campos necessários do `CONFIG.toml`.
4. Implementar `async def cotar(self, request: QuoteRequest) -> QuoteResponse`.
5. Implementar `async def cleanup(self) -> None` se abrir browser/context/page.
6. Registrar a transportadora em `_PROVIDER_SPECS` no `factory.py`.
7. Adicionar campos obrigatórios em `_REQUIRED_FIELDS`.
8. Adicionar exemplo em `app/CONFIG.example.toml`.
9. Adicionar testes de factory, validação mínima e contrato.

Esqueleto:

```python
from fretio.providers.base import ProviderBase
from fretio.quotation_contract import QuoteRequest, QuoteResponse


class NovaProvider(ProviderBase):
    def __init__(self, usuario: str, senha: str, headless: bool = True) -> None:
        super().__init__("nova")
        self.usuario = usuario
        self.senha = senha
        self.headless = headless

    async def cotar(self, request: QuoteRequest) -> QuoteResponse:
        try:
            # Abrir Playwright localmente, preencher portal e extrair resultado.
            return QuoteResponse.ok(
                provider=self.nome,
                valor_frete=100.0,
                prazo_dias=3,
                stage="resultado",
            )
        except Exception as exc:
            return QuoteResponse.error(
                provider=self.nome,
                detalhes="Falha técnica na cotação",
                error_code="falha_tecnica",
                stage="cotacao",
                raw={"error_type": type(exc).__name__},
            )

    async def cleanup(self) -> None:
        # Fechar page/context/browser/processos se existirem.
        return None
```

Registro no factory:

```python
def _build_nova(config: dict[str, object]) -> dict[str, object] | None:
    usuario = str(config.get("usuario") or "").strip()
    senha = str(config.get("senha") or "").strip()
    if not usuario or not senha:
        return None
    return {"usuario": usuario, "senha": senha, "headless": bool(config.get("headless", True))}


_PROVIDER_SPECS["nova"] = ProviderSpec(
    "nova",
    "fretio.providers.nova",
    "NovaProvider",
    _build_nova,
)
```

## Boas práticas

- Não tocar widgets PySide6 dentro do provider.
- Não criar event loop próprio.
- Não salvar senha em log.
- Tratar rota não atendida como `nao_atendido`, não como falha técnica.
- Tratar portal sem valor como `sem_cotacao`.
- Fechar browsers no cleanup.
- Manter mensagens de erro curtas e sanitizadas.
