# AGENT_PROVIDER_MAP.md

Mapa dos providers de transportadoras para agentes.

## Arquivos centrais

- `app/fretio/src/fretio/providers/base.py`: base dos providers, classificacao de erros e adaptacao entre contrato novo e legado.
- `app/fretio/src/fretio/providers/factory.py`: registro, validacao minima, leitura de config e criacao de providers.
- `app/fretio/src/fretio/quotation_contract.py`: `QuoteRequest`, `QuoteResponse` e sanitizacao.
- `app/cotacao/orchestrator.py`: prepara dados, valida regras e executa providers.
- `docs/PROVIDERS.md`: guia de criacao de provider.

## Contrato

Preferir `async def cotar(self, request: QuoteRequest) -> QuoteResponse`.

O legado `coteir(...)` ainda existe e e adaptado por `ProviderBase`.

Status permitidos:

- `ok`
- `sem_cotacao`
- `erro`
- `desabilitada`
- `nao_atendido`

## Providers registrados

- `braspress` -> `braspress_playwright.py`
- `trd` -> `trd.py`
- `agex` -> `agex.py`
- `eucatur` -> `eucatur.py`
- `rodonaves` -> `rodonaves.py`
- `alfa` -> `alfa.py`
- `coopex` -> `coopex.py`

## Regras de trabalho

- Nao tocar PySide6 dentro de provider.
- Nao criar event loop proprio dentro de provider.
- Playwright deve rodar fora da thread principal do Qt.
- Sempre preservar `cleanup()` quando o provider abrir browser, context ou page.
- Nao registrar dados sensiveis em log.
- Tratar rota fora de cobertura como `nao_atendido`.
- Tratar portal sem valor como `sem_cotacao`.
- Tratar falha tecnica como `erro`.

## Onde mexer

- Falha de uma transportadora: provider especifico.
- Transportadora nao aparece/cria: `factory.py`.
- Resultado padronizado errado: `quotation_contract.py`.
- Cotacao bloqueada antes de abrir portal: `app/cotacao/orchestrator.py`.
- UI trava: `app/async_worker.py` e `app/romaneio_app.py`.
- Campo de configuracao: `app/company_config.py`, `app/CONFIG.example.toml` e `factory.py`.

## ProviderBase

`ProviderBase` sanitiza detalhes, classifica erros, converte entrada nova para kwargs legados e converte resultado legado para `QuoteResponse`.

Classificacoes importantes:

- timeout
- login_falhou
- dados_invalidos
- valor_nao_encontrado
- falha_tecnica
- sem_cotacao
- nao_atendido

## Orquestrador

`app/cotacao/orchestrator.py` faz:

1. Aplica overrides seguros.
2. Lida com modo foco.
3. Gera resultados para providers desabilitados remotamente.
4. Valida origem, destino, UF, documento, peso, valor, volumes e cubagens.
5. Cria providers via factory.
6. Checa cobertura por UF.
7. Executa providers.
8. Emite progresso para UI.
9. Envia telemetria e jobs em modo best-effort.

## Prompt recomendado

```txt
Leia AGENTS.md, PROJECT_MAP.md e docs/AGENT_PROVIDER_MAP.md.
A tarefa envolve a transportadora <nome>.
Abra apenas o provider especifico, factory.py, quotation_contract.py e o trecho relevante do orchestrator.py.
Antes de alterar, diga quais arquivos pretende mexer.
Preserve cleanup e sanitizacao.
Explique como testar pela interface.
```
