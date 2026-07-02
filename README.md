# Fretio / RomaneioBeta Desktop

Aplicativo desktop Windows para operação de romaneios, cotação de frete e rastreio. A interface local é feita em pywebview/WebView2 (UI web) e as automações dos portais das transportadoras rodam no próprio desktop com Playwright/Chromium.

O servidor é usado como API central para licença, configuração remota, telemetria, logs sanitizados, jobs de cotação e descoberta de versão. Ele não recebe credenciais de transportadoras e não executa Playwright.

## Stack

- Python 3.12
- pywebview/WebView2 (UI web) para UI local
- Playwright + Chromium no desktop para automação dos portais
- PyInstaller + Inno Setup para build Windows
- Configuração por empresa em TOML

## Fluxo principal

1. O usuário abre o app e seleciona a empresa.
2. O app carrega `%APPDATA%\Fretio\empresas\<empresa>\CONFIG.toml`.
3. A licença é validada em `license_api_url`.
4. A configuração remota segura é buscada no servidor.
5. A UI web (pywebview/WebView2) dispara módulos locais: romaneio, cotação, frete fornecedores e rastreio.
6. Providers em `app/fretio/src/fretio/providers` usam Playwright localmente quando precisam acessar portais.
7. Eventos, erros e jobs são enviados ao servidor em modo best-effort, sempre sanitizados.

## Configuração

Use [app/CONFIG.example.toml](app/CONFIG.example.toml) como referência. Não versionar `CONFIG.toml`, chaves de licença, senhas ou arquivos de cache.

Exemplo mínimo, com valores fictícios:

```toml
[fretio]
version_api_url = "https://api.exemplo.com/api/version/latest"
license_api_url = "https://api.exemplo.com/api/licenses/validate"
license_config_api_url = "https://api.exemplo.com/api/licenses/config"
error_api_url = "https://api.exemplo.com/api/errors"
usage_api_url = "https://api.exemplo.com/api/usage/events"
quotation_jobs_api_url = "https://api.exemplo.com/api/quotations/jobs"
quotation_normalization_api_url = "https://api.exemplo.com/api/quotations/normalize"

[romaneio]
cep_origem = "01001000"

[transportadoras.trd]
habilitado = true
email = "usuario@example.com"
senha = "SENHA_LOCAL"
headless = true
ufs_atendidas = ["RS", "SC", "PR"]
```

## Desenvolvimento

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r installer\requirements.in
python -m playwright install chromium
python app\web_app.py
```

Em produção, a versão do app vem de `app/version.txt`.

## Build e atualização

O build do desktop fica em `installer/` e usa `installer/requirements-lock.txt`. O pacote de update é um ZIP com executável e `version.txt`; o app valida a estrutura do ZIP antes de aplicar.

```cmd
cd installer
build.bat
```

O fluxo de update consulta primeiro `version_api_url`. Se o servidor estiver indisponível, o updater usa GitHub Releases via `github_repo` e `github_repo_aliases`.

## Documentação

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): arquitetura local, threads, Playwright e integração com servidor.
- [docs/PROVIDERS.md](docs/PROVIDERS.md): como providers funcionam e como criar uma nova transportadora.
- [docs/LICENSING.md](docs/LICENSING.md): `license_api_url`, configuração remota e dados permitidos.
- [docs/UPDATE.md](docs/UPDATE.md): descoberta de versão, update obrigatório e fallback GitHub.
- [docs/RELEASE.md](docs/RELEASE.md): checklist de release segura e rollback.
- [CHANGELOG.md](CHANGELOG.md): mudanças por versão.

## Regra de segurança

O desktop nunca deve enviar ao servidor senhas de transportadoras, cookies, HTML bruto, screenshots, XML completo, DANFE/PDF completo, CPF/CNPJ completos de clientes finais, token GitHub, `ADMIN_TOKEN`, `DATABASE_URL` ou tracebacks sem sanitização.
