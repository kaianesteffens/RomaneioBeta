---
name: fretebot-provider
description: >-
  Use sempre que o usuário pedir para adicionar, criar, debugar ou ajustar uma transportadora (provider) no FreteBot/RomaneioBeta — qualquer menção a "nova transportadora", "nova carrier", "scraper de cotação", nomes como AGEX/BRASPRESS/RODONAVES/TRD/EUCATUR/ALFA/COOPEX/BAUER/BORNELLI/MENGUE/VIOPEX, ou pedidos para automatizar login/cotação em portal de frete brasileiro com Playwright. Cobre o padrão completo — subclasse de `ProviderBase`, login resiliente, preenchimento de formulário com máscaras BR (CNPJ/CEP/peso/valor), extração do resultado, retorno em `Cotacao`, registro em `__init__.py` e `CONFIG.example.toml`. Use também quando o usuário disser "o provider X não está pegando o resultado", "preciso atualizar os seletores da Y", ou enviar HTML capturado de um portal.
---

# Adicionando uma nova transportadora ao FreteBot

Este projeto cota frete em vários portais brasileiros. Cada transportadora vira um **provider**: uma classe assíncrona que abre o Chrome via Playwright, faz login no portal, preenche o formulário de cotação, extrai o resultado e devolve um `Cotacao`. Todas as providers herdam de `ProviderBase` (em `app/fretebot/src/fretebot/providers/base.py`) e seguem o mesmo "esqueleto" — quando você acerta esse esqueleto, o resto é só HTML do portal específico.

A documentação aqui está organizada para você ler de cima pra baixo na primeira vez e depois pular pros tópicos relevantes nas vezes seguintes.

## Antes de começar — colete o material do portal

O usuário tipicamente já capturou os HTML dos elementos importantes do portal e salvou em `app/<NOME>/`. Cada arquivo `.txt` é o `outerHTML` de um elemento (input, botão, modal). Por exemplo `app/AGEX/LOGIN.txt`, `app/BRASPRESS/COTAÇAO BRASPRESS.txt`, `app/TRD/POP UP CONTINUAR.txt`.

Antes de escrever uma linha, **leia cada um dos `.txt` da pasta da transportadora alvo**. Esses arquivos te dão:

- nomes de `input` (`name=`, `placeholder=`) e `id` para construir locators robustos
- texto de botões (`button>Continuar</button>`) — prefira `get_by_role("button", name=...)` ao invés de classe CSS, porque classes do tipo `sc-bkEOxz gDUGnA` mudam a cada deploy
- presença de modais e popups que travam o fluxo (ex.: TRD tem "POP UP CONTINUAR")
- formato exato do resultado (ex.: AGEX mostra `Frete: <span>R$ 118,90</span>` num bloco com `id="resultado"`)

Se a pasta não existir ainda, peça ao usuário pra capturar usando o DevTools (F12 → botão direito no elemento → Copy → Copy outerHTML) e salvar em `.txt`. É bem mais barato do que adivinhar seletores.

## A arquitetura em uma página

```
app/fretebot/src/fretebot/providers/
├── base.py                 # ProviderBase (abstract) + launch_browser_resilient + find_chrome
├── __init__.py             # Reexporta cada Provider para o app principal usar
├── braspress_playwright.py # Exemplo de portal "tradicional" (form HTML, multipart)
├── agex.py                 # Exemplo de SPA (Next.js, localStorage para sessão)
├── trd.py                  # Exemplo com popup de confirmação no meio do fluxo
├── rodonaves.py            # Exemplo com captcha hidden (cf-turnstile) e captura de XHR
└── ...
```

Importações esperadas no topo de qualquer provider:

```python
from datetime import datetime
from typing import Optional
import re
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from fretebot.providers.base import ProviderBase, launch_browser_resilient
from fretebot.models import Cotacao
from fretebot.logging_conf import get_logger

logger = get_logger(__name__)
```

## Decisões importantes antes de codar

1. **Portal SPA (React/Next/Vue) ou tradicional?** SPAs (AGEX, parte do TRD) guardam autenticação em `localStorage`/`sessionStorage` — não dá pra abrir nova `page` entre cotações, senão perde a sessão. Em SPAs use `page.goto("/inicio")` pra resetar o formulário. Portais tradicionais (Braspress, Rodonaves) podem usar `_context.new_page()` à vontade entre cotações desde que mantenham os cookies do contexto.

2. **Login por CNPJ ou e-mail?** Olhe o input no HTML capturado: `name="document"`, `name="login"`, `name="email"`, `placeholder="CNPJ"`. Isso decide a assinatura do `__init__`.

3. **Tem antibot/captcha?** Se o portal usa Cloudflare Turnstile (`cf-turnstile`), reCAPTCHA, ou tem detecção heurística (mexer o mouse, digitar com delay), você vai precisar do utilitário `_simular_interacao_humana` que tem em `rodonaves.py` (movimentação Bezier do mouse). Se for Braspress, **use sempre** `launch_browser_resilient` da base — o Chrome real (não o Chromium do Playwright) é o que passa.

4. **Como o portal devolve o resultado?** Pode ser HTML renderizado, XHR JSON capturado via `page.on("response", ...)`, ou redirect pra outra página. Confira o arquivo `RESULTADO COTAÇAO.txt` da pasta — se ele for um `<div>` HTML, você vai fazer `page.locator(...).inner_text()`. Se for um `data:application/json` ou se você não conseguir achar o valor no DOM, é XHR — veja como `rodonaves._capture_quotation_response` funciona.

## O esqueleto que você vai escrever

O template completo está em `templates/provider_template.py` — copie ele pra `app/fretebot/src/fretebot/providers/<nome>.py` e adapte. Os blocos críticos:

### `__init__` — declare tudo que vem do CONFIG.toml

Toda transportadora tem credenciais e parâmetros de carga vindos do CONFIG. Declare cada um como argumento com default seguro:

```python
def __init__(
    self,
    cnpj: str = "",
    senha: str = "",
    cnpj_remetente: Optional[str] = None,
    cep_origem: Optional[str] = None,
    headless: bool = True,
    ufs_atendidas: Optional[list[str]] = None,
) -> None:
    super().__init__(nome="MinhaTransportadora")
    self.cnpj = self._digits(cnpj)
    self.senha = senha
    self.cnpj_remetente = cnpj_remetente or cnpj
    self.cep_origem = self._digits(cep_origem or "")
    self.headless = headless
    self.ufs_atendidas = [u.upper() for u in (ufs_atendidas or [])]
    self._pw = None
    self._browser = None
    self._context = None
    self._page = None
    self._logged_in = False
    self.last_error: str | None = None
```

Sempre inicialize `_pw/_browser/_context/_page` como `None` e `last_error` como `None`. O app principal lê `provider.last_error` quando a cotação falha pra mostrar a razão na interface.

### `_init_browser` — sempre via `launch_browser_resilient`

```python
async def _init_browser(self) -> None:
    if self._browser is not None:
        return
    self._pw = await async_playwright().start()
    self._browser = await launch_browser_resilient(self._pw, headless=self.headless)
    self._context = await self._browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        locale="pt-BR",
    )
    self._page = await self._context.new_page()
    self._page.set_default_timeout(30000)
```

Use **sempre** `launch_browser_resilient` da `base.py`. Ela:
- usa o Chrome instalado no sistema (não o Chromium empacotado), o que passa em vários antibots
- abre via CDP em porta livre, com profile temporário
- tenta 3x se o driver Node.js cair
- garante kill da árvore de processos no `close()`

Não chame `pw.chromium.launch()` direto — vai funcionar local mas quebrar no instalador.

### `_login` — idempotente e tolerante

```python
async def _login(self) -> bool:
    if self._logged_in:
        return True
    try:
        await self._page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        # Use seletores estáveis: name, placeholder, role. Evite classes geradas (sc-xwuxA).
        await self._page.locator('input[name="document"]').fill(self.cnpj)
        await self._page.locator('input[type="password"]').fill(self.senha)
        await self._page.get_by_role("button", name=re.compile("Iniciar|Entrar|Acessar", re.I)).click()
        # Aguarde indicador de login bem-sucedido (URL muda, ou aparece menu).
        await self._page.wait_for_url(re.compile(r".*(painel|inicio|dashboard).*"), timeout=15000)
        self._logged_in = True
        return True
    except Exception as e:
        self.last_error = f"Falha no login {self.nome}: {e}"
        logger.error(self.last_error)
        return False
```

Por que `if self._logged_in: return True`? Porque o app principal chama o provider **uma vez por linha do romaneio** — se a sessão ainda está ativa, pular login economiza 5–15s por cotação.

### `coteir` — o método público (sim, com erro de digitação)

A interface abstrata é `coteir` (com "i") em vez de `cotar`. **Mantenha** o nome — o `extrator_pedidos.py` chama exatamente esse. Se quiser, exponha um alias:

```python
async def cotear(self, *args, **kwargs):
    return await self.coteir(*args, **kwargs)
```

A assinatura padrão:

```python
async def coteir(
    self,
    origem: str,
    destino: str,
    peso: float,
    valor: float,
) -> Optional[Cotacao]:
```

Argumentos extras (volumes, cubagens, tipo_pagador, etc.) podem ser passados via `kwargs` ou setados antes via método `atualizar_carga()` — veja `agex.py` que faz isso explicitamente.

O corpo do método sempre faz:

```python
async def coteir(self, origem, destino, peso, valor) -> Optional[Cotacao]:
    try:
        self.last_error = None
        await self._init_browser()
        if not await self._login():
            return None  # last_error já setado
        if not await self._preencher_cotacao(origem, destino, peso, valor):
            return None
        resultado = await self._extrair_resultado()
        if not resultado:
            self.last_error = self.last_error or f"{self.nome} sem resultado"
            return None
        valor_frete, prazo_dias, restricoes = resultado
        logger.info(f"[{self.nome}] OK R$ {valor_frete:.2f} - {prazo_dias}d")
        return Cotacao(
            transportadora=self.nome,
            prazo_dias=prazo_dias,
            valor_frete=round(valor_frete, 2),
            restricoes=restricoes,
            timestamp=datetime.now(),
        )
    except Exception as e:
        self.last_error = str(e)
        logger.error(f"[{self.nome}] Erro na cotação: {e}", exc_info=True)
        return None
```

### `cleanup` — sempre fechar tudo

```python
async def cleanup(self) -> None:
    for closer, attr in (
        (self._page, "_page"),
        (self._context, "_context"),
        (self._browser, "_browser"),
        (self._pw, "_pw"),
    ):
        if closer is None:
            continue
        try:
            await (closer.stop() if attr == "_pw" else closer.close())
        except Exception:
            pass
        setattr(self, attr, None)
    self._logged_in = False
```

O app principal chama `await provider.cleanup()` no fim do batch. Se você esquecer, processos Chrome ficam órfãos no Task Manager do usuário — uma das reclamações mais comuns que aparecem nos issues.

## Helpers que vale a pena ter no provider

Veja `references/parsing-patterns.md` para utilitários reutilizáveis: `_digits`, `_parse_decimal_any` (lida com `1.234,56` e `1,234.56`), `_format_decimal_br_2`, `_normalizar_cubagens_cm`, `_extrair_valor_frete_do_texto`. Eles aparecem repetidos em quase todo provider — seria bom consolidar em `base.py`, mas por enquanto basta copiar do provider mais parecido com o seu.

## Padrões de seletor — o que funciona e o que quebra

Veja `references/login-patterns.md` para a tabela completa. Resumo do que importa:

- **Bom**: `input[name="cnpj"]`, `input[placeholder="CNPJ"]`, `get_by_role("button", name="Continuar")`, `get_by_label("Senha")`, `text="Resultado"`.
- **Frágil**: `.sc-bkEOxz`, `div > div:nth-child(3)`, `xpath=/html/body/...`. Classes "styled-components" (`sc-XXXXX`) e `nth-child` mudam a cada redeploy do portal.
- **Modais e popups**: tente fechar com `get_by_role("button", name=re.compile("Fechar|OK|Continuar|Entendi", re.I))` antes do timeout.
- **Inputs com máscara**: alguns campos só aceitam digitação tecla-a-tecla (`page.keyboard.type("...", delay=50)`) e ignoram `fill()`. Veja `rodonaves._fill_field`.

## Captcha, antibot e Cloudflare

- **Cloudflare Turnstile** (`cf-turnstile`): pegue o `data-sitekey`, resolva via API externa (2Captcha, etc.), e injete via `cf-turnstile-response`. `rodonaves.py:_captcha_token` mostra como.
- **Detecção comportamental** (Braspress): use `launch_browser_resilient` (Chrome real), `--disable-blink-features=AutomationControlled` (já vem na base), e `_simular_interacao_humana` se necessário.
- **Honeypot e CSRF token**: leia o `<input type="hidden" name="CSRF_TOKEN">` antes do submit; se o portal regenera o token a cada page load, **navegue de novo** antes de tentar de novo.

## Registrando o provider no app

Quando o arquivo `<nome>.py` estiver pronto, atualize 3 lugares:

1. **`app/fretebot/src/fretebot/providers/__init__.py`** — adicione `from fretebot.providers.<nome> import <Nome>Provider` e inclua em `__all__`.

2. **`app/CONFIG.example.toml`** — adicione uma seção `[transportadoras.<nome>]` com `habilitado = false` por default e todas as chaves do `__init__` documentadas.

3. **`installer/FreteBot.spec`** — adicione `"fretebot.providers.<nome>"` à lista `hiddenimports`. PyInstaller não detecta imports dinâmicos, então sem isso o `.exe` empacotado não vê o provider.

Se o provider tem logo, jogue `app/assets/logos/<nome>.png` (idealmente 64x64 PNG transparente) — o `FreteBot.spec` já varre essa pasta.

## Testando

Crie um script `test_<nome>.py` na raiz de `app/`:

```python
import asyncio
from fretebot.providers.<nome> import <Nome>Provider

async def main():
    provider = <Nome>Provider(cnpj="...", senha="...", headless=False)
    try:
        cot = await provider.coteir(
            origem="99740000",  # CEP origem
            destino="01310100", # CEP destino (Av. Paulista)
            peso=10.0,
            valor=500.0,
        )
        print(cot)
        if not cot:
            print("ERRO:", provider.last_error)
    finally:
        await provider.cleanup()

asyncio.run(main())
```

Rode com `headless=False` na primeira vez pra ver o que tá rolando. Quando estiver consistente, mude pra `True` e teste de novo — alguns portais se comportam diferente em headless (esconder elementos, atrasar render).

## Erros comuns que você vai enfrentar

| Sintoma | Causa provável | Solução |
|---|---|---|
| `Chrome encerrou inesperadamente` | Chrome local não instalado ou path errado | `find_chrome()` em `base.py` levanta erro claro — instale o Chrome |
| `Timeout 30000ms exceeded` no login | Seletor frágil ou modal cobrindo | Capture HTML de novo, prefira role/text, feche modais antes |
| Funciona local, falha no `.exe` | Provider faltando em `hiddenimports` | Adicione no `FreteBot.spec` e rebuild |
| Sessão perdida entre cotações em SPA | Você abriu `new_page()` | Em SPA reaproveite `self._page` e faça `goto("/inicio")` |
| `last_error: ''` mas `cotear` retorna `None` | Esqueceu de setar `last_error` | Sempre defina antes de retornar `None` |
| Resultado vazio mesmo aparecendo no browser | Portal usa XHR que carrega depois | Capture via `page.on("response", ...)` em vez de scrape do DOM |

## Quando o usuário só pediu pra "consertar" um provider existente

Se a queixa é `"a transportadora X parou de funcionar"`, antes de mexer:

1. Rode o provider em `headless=False` e olhe o que acontece.
2. Compare o HTML renderizado com o `.txt` capturado em `app/<NOME>/`. Se mudaram, atualize o `.txt` E os seletores.
3. Verifique se o portal trocou a URL de login/cotação (constantes `LOGIN_URL` e `COTACAO_URL` no topo da classe).
4. Se aparece "Erro de credenciais" mas o login está certo: o portal pode ter ativado MFA, ou bloqueou IP — cheque manualmente no navegador antes de mudar código.

Mantenha sempre o `last_error` informativo — ele é o que o usuário vê na coluna "Status" do romaneio.
