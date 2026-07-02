# Changelog

## 2.55 - 2026-07-02

- Migração da UI desktop de PySide6 para UI web renderizada em WebView2 via pywebview: front local em `app/web/*` (`index.html`, `app.js`, `app.css`, `format.js`, `pages/*.js`), com `app/web_app.py` expondo a bridge `Api`, `app/web_presenters.py` montando os dados e `app/app_bootstrap.py`/`app/startup.py` cuidando de startup, licença, configuração remota e update.
- Comando de desenvolvimento passa a ser `python app/web_app.py` (ou `app/dev.bat`); a UI antiga PySide6 foi removida.
- Providers Playwright/Chromium seguem inalterados, rodando localmente no desktop.
- Auditoria de segurança: revisão de segredos, sanitização de logs/diagnósticos de providers e unificação do fluxo de releases.

## 2.32 - 2026-05-31

- Integração com RomaneioBeta-server para licenças, configuração remota, telemetria, erros, jobs de cotação e descoberta de versão.
- Compatibilidade mantida com validação legada via GitHub Gist quando `license_api_url` não estiver configurado.
- Uso offline preservado quando há cache de licença válido e o servidor está indisponível.
- Configuração remota tolerante a endpoint ausente, resposta inválida ou falha de rede, usando cache/defaults.
- Sanitização reforçada para erros, eventos de uso, jobs e payloads de cotação antes de envio ao servidor.
- Workflow Windows gera instalador, ZIP de update, launcher e assets estáveis para o repositório de releases.
- ZIP de update validado e assinado quando chaves de assinatura estiverem configuradas.
- Documentação de deploy, backup, update e licenciamento atualizada.
