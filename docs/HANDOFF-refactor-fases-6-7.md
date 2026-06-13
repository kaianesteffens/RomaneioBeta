# Handoff — Refatoração Fases 6 e 7

> Documento auto-contido para **continuar a refatoração numa sessão nova**, sem
> depender do histórico do chat anterior. Leia este arquivo inteiro, depois
> `AGENTS.md` e `PROJECT_MAP.md`, antes de mexer em qualquer coisa.

## Onde estamos

- Branch de trabalho: **`refactor/codebase-cleanup`** (criada a partir de `master`).
  `master` está **intacto** e **nada foi pushed**. Trabalhe nesta branch.
- Estado: **20 commits**, **767 testes verdes**, pacote `app/cotacao/` **pyflakes-limpo**.
- O app foi migrado de PySide6 para **UI web** (pywebview/WebView2): shell em
  `app/web_app.py` + `app/web/*` (HTML/CSS/JS). Backend Python intacto.
- Memória do projeto: `refactor-codebase-cleanup` (resumo) e `ui-web-migration`.

### Commits já feitos (não refazer)

| Commit | Fase | Conteúdo |
|--------|------|----------|
| `31635fe` | 0 | Baseline da migração PySide6→web (ponto de restauração) |
| `e4b2666` | 1 | Segurança: 6 vazamentos de provider fechados, JS-injection, senha, updater, regex |
| `7231abd` | 2 | −594 linhas de código morto; CNPJ mascarado em logs |
| `1446494` | 3 | Imports explícitos nos 4 módulos quentes da cotação + `common.__all__` curado |
| `53c8dc7` | 4 | `cotacao/deps.py` (DI) substitui `_sync_legacy_overrides` |
| `bdb1e32` | 5 | 69 testes de caracterização + **fix do `_dados_envio`** (cotação por PDF voltou) |
| `ecd7d6d` | 6 (fatia) | `_finalizar` extraído no orchestrator (9 blocos duplicados colapsados) |
| `c0f5138` | 7 (fatia) | Teardown determinístico ao fechar (encerra Chrome/Playwright) |
| `c705b63` | — | UX: Cotação desacoplada do Romaneio (eram independentes; era só texto enganoso) |
| `cbd113e` | 6 passo 1 | Golden dispatch do orchestrator (10 casos, pré-decomposição) |
| `123117b` | 6 passo 1 | 7-tupla de `_run_cotacao` → dataclass `CotacaoOutcome` |
| `35bd1ad` | 6 passo 1 | `_processar_resultado` → dispatch + handlers (`_handle_*`) |
| `dc0f206` | 6 passo 2 | Parity test do conhecimento por transportadora (test-first) |
| `6b326c5` | 6 passo 2 | Registry único em `ProviderSpec` (required/credential/slowness) |
| `a12de27` | 6 passo 2 | `web_app._CARRIER_FIELDS` deriva do registry |
| `22d4a1b` | 6 passo 2 | `session_manager._PRIORIDADE_LENTIDAO` deriva do registry |
| `ad8d7a2` | 6 passo 3 | Teste por-padrão dos classificadores de erro (test-first) |
| `e69cd64` | 6 passo 3 | `cotacao/error_classifiers.py` unifica os 3 classificadores |
| `3d02d85` | 7 passo 1 | Golden da superfície pública de `web_app.Api` (30 métodos) |
| `196a065` | 7 passo 1 | Extrai `ConfigMixin` de `web_app.Api` (1º delegate) |

**Fase 6 passos 1 e 3 = CONCLUÍDOS.** Passo 2 = núcleo seguro feito (registry +
3 consumidores deduplicados + parity test). Falta o que está marcado abaixo
(passos 4 e 5 — providers, ALTO RISCO — e o resto do passo 2).

## Como verificar (rode SEMPRE a cada passo)

```bash
cd C:/Users/wined/Projetos/RomaneioBeta
# Suíte completa (cada teste insere seu sys.path; QT offscreen é exigido por deps de teste)
QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider          # esperado: 739 passed
# Regressão de provider (o checo mais rápido ao mexer em provider)
QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_provider_regressions.py
# Lint de nomes indefinidos / imports mortos (pip install pyflakes se faltar)
python -m pyflakes app/cotacao/*.py app/cotacao_transportadoras.py
# Screenshot headless de qualquer tela da UI web (mesmo motor do WebView2)
python .claude/skills/run-romaneio/web_shot.py --page cotacao --out C:/Users/wined/AppData/Local/Temp/x.png
```

Princípio: **suíte verde após cada passo, um commit por passo.** Nunca avance com
testes vermelhos.

## Testes que PROTEGEM o trabalho (a rede de segurança)

- `test_char_orchestrator_builders.py` (34): os 8 `_build_*_kwargs` e o dispatch
  `QuoteResponse`/legado/`None` → `ResultadoCotacao`. **Protege a Fase 6 do orchestrator.**
- `test_char_web_app_serializers.py` (17): serializers da ponte, formato exato do
  romaneio FOB, sentinela de senha. **Protege a Fase 7.**
- `test_char_web_app_teardown.py` (6): teardown ao fechar.
- `test_char_circuit_breaker_config.py` (18): circuit breaker + `config._dados_envio`.
- `tests/test_provider_regressions.py`: invariantes dos providers que **codificam bugs
  de produção antigos** — trate como contrato rígido (não recriar page, esconder
  sempre após mostrar, um clique nativo só).

> **Regra de ouro:** para QUALQUER decomposição de função grande ou provider, escreva
> primeiro o teste de caracterização que fixa o comportamento atual (golden), confirme
> verde, e só então refatore. Veja "tests needed first" no fim deste doc.

---

## FASE 6 — Decompor orquestrador + providers (ALTO RISCO)

Já feito: `_finalizar` no `_processar_resultado` (`ecd7d6d`). Falta:

1. **[CONCLUÍDO — `cbd113e`/`123117b`/`35bd1ad`] orchestrator `_processar_resultado` / `_run_cotacao`**:
   - `_processar_resultado` dividido em handlers (`_handle_exception` / `_handle_quote_response`
     / `_handle_legacy` / `_handle_none` + `_handle_erro_simples` defensivo) com um dispatch chain.
   - 7-tupla de `_run_cotacao` trocada por dataclass frozen `CotacaoOutcome` (escopo do
     orchestrator). Guarda `len==7` virou `isinstance`.
   - Rede de segurança: `test_char_orchestrator_dispatch.py` (golden e2e das 7 formas +
     efeitos no circuit breaker + progresso) — **rode-o a cada mudança no dispatch**.

2. **[PARCIAL] Registry único por transportadora** (`app/fretio/src/fretio/providers/factory.py`):
   - **FEITO**: `ProviderSpec` agora é o registro único e carrega `required_fields`,
     `credential_fields` (chave/rótulo/tipo da UI) e `slowness_priority`. `_REQUIRED_FIELDS`,
     `web_app._CARRIER_FIELDS` e `session_manager._PRIORIDADE_LENTIDAO` **derivam** dele.
     Acessores: `credential_fields_for_provider`, `slowness_priority_for_provider`. Protegido
     por `test_carrier_registry_parity.py`.
   - **FALTA (decidir antes — alto risco)**: dirigir os **8 blocos de setup do orchestrator**
     (UF filter já é uniforme via `_uf_atendida`; `headless policy` é per-carrier; create_kwargs
     e predicados especiais divergem muito) e centralizar `KNOWN_CARRIERS` (hoje em
     `remote_permissions`, **não** rewireado — risco de ciclo de import na cadeia de licença;
     o parity test já impede drift). **PRESERVE**: Rodonaves `headless=False` pinado, exclusões
     AGEX (RS/SC), ALFA PICOLO. Protegido pelos 34 char tests dos `_build_*_kwargs` + o golden dispatch.

3. **[CONCLUÍDO — `ad8d7a2`/`e69cd64`] Unificar os 3 classificadores de erro**: novo
   módulo `app/cotacao/error_classifiers.py` é a fonte única de `_is_business_error`
   (+`_BUSINESS_PATTERNS`), `_is_expected_transient_failure(_str)`/`_TRANSIENT_PATTERNS` e
   `is_expected_prelogin_failure`/`_PRELOGIN_CONTROLLED_PATTERNS`. `orchestrator` e
   `error_context` reexportam as funções (compat). Teste por-padrão:
   `test_error_classifiers.py` (+ identidade de fonte única).

4. **Deduplicação de providers** (protegido por `test_provider_regressions.py` — mas
   verifique **cópia por cópia** que são byte-idênticas antes de unificar; muitas têm
   variações sutis por transportadora):
   - Loop de auto-complete de CEP (TRD ×4) → helper compartilhado.
   - Parse de R$/prazo (Rodonaves ×4, COOPEX/EUCATUR) → helper em `provider_utils.py`.
   - JS de native-setter (~12 cópias) → helper em `provider_utils.py`.
   - **NÃO** toque nos blocos de captcha/CDP/Turnstile nem em timings/seletores (ver DO NOT TOUCH).

5. **Unificar COOPEX e EUCATUR** num `SswProvider` base (~95% idênticos) parametrizado
   por nome/wait/cuba-grouping, adotando o `_cleanup_step` seguro do EUCATUR. Mantenha
   nomes de campo e funções JS de trigger **byte-idênticos** (ver DO NOT TOUCH).
   - **Pergunta em aberto a resolver antes**: a lógica de cuba diverge — COOPEX agrupa
     por dimensão, EUCATUR expande direto. Isso é regra intencional por carrier ou drift
     acidental? Pode mudar a cotação de romaneios multi-caixa. Decida antes de unificar.
   - **Pergunta em aberto**: SSW aceita CPF (11 díg) de pagador? Orchestrator aceita 11 ou
     14, mas os providers exigem 14 e falham tarde — decidir rejeitar antes ou afrouxar.

**Bloqueio de cobertura**: `extrator_pedidos.py` (1590 linhas, parsing de PDF) e a
extração de DOM dos providers e `rastreamento.py` têm **cobertura zero**. Decompor sem
fixtures reais (PDFs/HTML de cliente) é arriscado. Para esses, ou escreva golden tests
com amostras representativas primeiro, ou **limite-se** aos refactors já cobertos pela
regressão de provider. Não mexa no parsing sem fixture.

---

## FASE 7 — Dividir ponte web + aposentar contrato legado (RISCO MÉDIO)

Já feito: teardown determinístico (`c0f5138`). Falta:

1. **[EM ANDAMENTO] Dividir `web_app.Api`** (~800 linhas, 1 god-object) em delegates por domínio.
   - **FEITO**: golden da superfície pública (`test_char_web_app_api_surface.py`, 30 métodos +
     assinaturas exatas — o contrato pywebview) e **`ConfigMixin` extraído** (`196a065`).
   - **Padrão provado (use para os demais)**: mixin por domínio definido ANTES de `class Api`,
     com os corpos movidos **verbatim** (continuam operando sobre `self`, então a superfície
     pywebview e o estado compartilhado ficam idênticos). Mecânica por delegate: (1) inserir
     `class XMixin:` + reparent `class Api(ConfigMixin, XMixin, ...)`; (2) remover os métodos
     originais do `Api`. Verificar a CADA delegate: `python -m pytest -q test_char_web_app_api_surface.py
     test_char_web_app_serializers.py test_char_web_app_teardown.py` + `python app/web_app.py --smoke OUT`
     + suíte completa.
   - **FALTA**: `StartupMixin` (bloco contíguo `startup_*` + `get_bootstrap`/`listar_empresas`),
     `RastreioMixin` (`rastreio_*` + `_ser_rastreio`/`_coro_rastreio`), `CotacaoMixin`
     (`cotacao_iniciar`/`_coro_cotacao`/`_ser_resultado`/`_cb_*`/`fornecedor_cotar`/`nfe`/`romaneio`/`dashboard`).
   - **Mantenha cada nome/assinatura público IDÊNTICO** — `pywebview.api.<nome>` e o envelope
     `onBackendEvent({event,payload})` não podem mudar. Protegido pelos 17 char serializers + o golden de superfície.

2. **Mover lógica de apresentação para fora da ponte**: `_nota_card`,
   `_validar_local_entrega`, `_montar_romaneio_fornecedor` → um formatter de domínio
   (ex.: módulo de romaneio/extrator_nfe) reutilizado. **Pin o formato FOB com golden
   test antes** (o char test já cobre o formato exato — não mude a string sem atualizar).
   - Quirk conhecido (caracterizado, decida se corrige): o TOTAL do romaneio FOB sai
     `R$ 1234.56` (ponto, sem milhar) em vez de `R$ 1.234,56`. O parser aceita ambos, então
     é cosmético — só corrija com cuidado para não quebrar o handoff.

3. **Guardas de concorrência**: adicionar guarda `_rastreando` em andamento e tornar
   `_cotando` thread-safe.

4. **Aposentar o contrato legado** — só DEPOIS de migrar todos os providers:
   - **Pergunta em aberto**: quantos dos 8 providers já aceitam um `cotar()` com forma
     `QuoteRequest`? Faça uma auditoria por-provider. Enquanto houver provider no caminho
     legado, **mantenha** o fallback `coteir` + `cotacao_legada_to_quote_response`
     (`app/fretio/src/fretio/quotation_contract.py`) — é load-bearing.
   - Quando todos migrarem: remover `coteir` fallback e `cotacao_legada_to_quote_response`
     **e** `resultado_cotacao_to_quote_response` (que ficou pendente da Fase 2, é API de
     contrato pública só usada por testes hoje).
   - **NÃO** renomeie `coteir` (ver DO NOT TOUCH) — `base.cotar` despacha por esse nome
     exato nos 8 providers; rename só em PR atômico dedicado, com a regressão verde.

5. **Limpeza do frontend** (`app/web/`): de-duplicar máscaras de CNPJ/CEP e o ciclo de
   vida do driver de cotação em helpers compartilhados; fazer `window.Pages` o registro
   único; consolidar `$`/`apiBridge`/`toast` num core carregado primeiro (hoje `app.js`
   carrega por último — funciona porque `$`/`apiBridge` são `const` de escopo global de
   script clássico, mas é frágil; mover para `format.js` que carrega primeiro).

---

## DO NOT TOUCH (zonas frágeis — só mudança cirúrgica e bem justificada)

- **Ed25519** (`update_security.py:131-147`) — fail-closed; chave embutida ausente DEVE
  levantar, nunca pular. Só sanitize a string de versão/tag.
- **Updater .bat + flags de subprocess** (`updater.py:578-757`) —
  `CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW`, sem `DETACHED_PROCESS`, timing de PID-wait
  são deliberados. Só adicione sanitização de input.
- **Rodonaves captcha/CDP** (`rodonaves.py:1156-1255, 977-1101, 1786-1935`) — janela
  off-screen (-32000,-32000), wait/manual-submit do reCAPTCHA, re-hide no finally, stealth
  JS, `headless=False` pinado. Não reordene nem recrie page em erro transitório.
- **ALFA Turnstile CDP-raw** (`alfa.py:283-394, 1012-1118`) — frames WebSocket 0x81 crus,
  ordem fill-via-CDP-depois-connect-Playwright, toggling Win32 off-screen.
- **SSW internals COOPEX/EUCATUR** — nomes de campo `f2/f4/f6.../cuba1..11`, funções JS
  `pag/ce2/cep/f_c/sim`, reaplicação de `f15`, `f4='001'` — byte-idênticos ao extrair o SswProvider.
- **TRD/Braspress** — login SSO Keycloak/iframe, contagens/timeouts do poll de CEP,
  fechadores de modal, scan result-anywhere — extraia helpers, **nunca** mude timings/seletores.
- **Cadeia de licença** (`license.py` validate/grace-cache + `remote_config`) — payload já
  é mínimo `{key, machine_id}`; não adicione campos. Mudar `get_machine_id` re-vincula
  todas as instalações de uma vez.
- **Mutex de instância única** `Local\\Fretio.Singleton.v1` + `ERROR_ALREADY_EXISTS=183`
  e enforcement de instalação canônica (`app_bootstrap.py:19-103`) — strings exatas;
  retornos fail-open são intencionais.
- **Migração AppData FreteBot→Fretio** (`startup.py:67-194`) — merge não-destrutivo
  irreversível one-shot; só refatore com fixture de teste de migração.
- **crash.log stderr redirect + filtro `_IGNORE`** (`app_bootstrap.py:106-153`) —
  reatribui `sys.stderr` no processo todo; o filtro mira ruído real de teardown do asyncio.
- **`error_context._sanitize_text` / `sanitize_context`** — o sanitizador mais forte, última
  linha de defesa; só ADICIONE padrões (com teste), nunca reordene/afrouxe.
- **`coteir` + dispatch `inspect.signature` do `base.cotar`** (`base.py:131-238`) — load-bearing
  nos 8 providers; rename só em PR atômico dedicado.
- **Asserts da regressão de provider** — codificam bugs antigos; contrato rígido.
- **session_manager**: ordem de pré-login, semáforo `concurrency=2`, ALFA fora do semáforo,
  e o filtro de user-data-dir do `_kill_orphan_Fretio_chromes` — timing-tuned e crítico
  (matching de chrome amplo demais mataria o Chrome pessoal do usuário).

## Perguntas em aberto — RESOLVIDAS (decisões de 2026-06-13)

1. **Cuba COOPEX vs EUCATUR**: a auditoria mostrou que os campos cuba enviam o MESMO
   conjunto (só a ordem difere — cosmético p/ o SSW). A divergência real é no `coteir`:
   EUCATUR bloqueia se `volumes≠Σcubagens`, COOPEX sobrescreve em silêncio. **DECISÃO:
   o `SswProvider` unificado adota o comportamento da COOPEX (sobrescrever volumes pela
   soma)** — EUCATUR deixa de bloquear. Caracterizar antes de unificar (passo 5).
2. **SSW aceita CPF (11 díg)?** Providers exigem 14 e falham TARDE (após browser+login).
   **DECISÃO: Opção A — rejeitar cedo no orchestrator** (espelhar o guard de 14 dígitos da
   RODONAVES; mensagem clara via `_resultado_documento_pagador_ausente`, stage `validacao`),
   antes de abrir o browser.
3. **Quantos providers têm `cotar(QuoteRequest)`?** Auditado: **7 de 8** passam pelo seletor
   `_provider_supports_quote_request_cotar` (só **translovato** continua no `coteir`). MAS os 7
   só fazem `return await super().cotar(request)`, que cai de volta no `coteir` via `ProviderBase`.
   Aposentar o legado (Fase 7) exige (a) dar um `cotar` próprio à translovato e (b) reescrever a
   lógica real de cada provider p/ consumir `QuoteRequest` direto. **Grande — não é quick win.**
4. **`coteir` é typo ou legado deliberado?** Começou como typo, hoje é **legado load-bearing**
   (abstrato em `base.py`, sobrescrito nos 8, despachado por nome via `inspect.signature`, +
   `--deselect` no CI). **NÃO renomear** (DO NOT TOUCH); só em PR atômico dedicado.
5. `resultado_cotacao_to_quote_response` pode sair junto com o legado na Fase 7.

## Regras do projeto (de AGENTS.md / CLAUDE.md)

- Windows é o alvo oficial. Linux só dev/test interno — nunca mude regra de negócio por Linux.
- **Política de comentários**: só comentar o "porquê" não-óbvio (regra de negócio, fluxo
  frágil de portal, captcha, workaround). Não descrever o que o código já diz.
- **Fronteira de segurança**: o desktop NUNCA envia ao servidor senha, cookie, HTML cru,
  XML/PDF/DANFE completo, CNPJ/CPF completo, traceback não-sanitizado ou CONFIG.toml.
- Para providers: seletores robustos (name/placeholder/role/texto), `last_error` informativo,
  cleanup de browser/context/page. Siga a arquitetura de `providers/`.
- Trabalhe em fatias pequenas, suíte verde por fatia, um commit por fatia, verificação
  adversarial nas partes de risco.

## "Tests needed first" (escreva ANTES de decompor o alvo)

- Provedores TRD(`cotear`)/Rodonaves(`_submeter_e_extrair`)/COOPEX/EUCATUR(`_submeter_e_extrair_inner`):
  golden de result-text + CEP-autocomplete antes de colapsar loops/regexes.
- `extrator_pedidos` parsing + bin-packing (`_extrair_itens`, `_adicionar_item_cabo_kit`,
  `_empacotar_complementos`): golden em PDFs KIT representativos (cobertura ZERO, risco MUITO ALTO).
- `rastreamento` shapes por handler de carrier (cobertura zero) antes de extrair o
  classificador de erro de rede.
- Parity test: lista de carrier embutida / `_CONFIG_FALLBACK` vs `remote_permissions` e
  `factory._PROVIDER_SPECS` antes de centralizar no registry.
