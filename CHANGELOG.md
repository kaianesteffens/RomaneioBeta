# Changelog

## 2.32 - 2026-05-31

- Integração com RomaneioBeta-server para licenças, configuração remota, telemetria, erros, jobs de cotação e descoberta de versão.
- Compatibilidade mantida com validação legada via GitHub Gist quando `license_api_url` não estiver configurado.
- Uso offline preservado quando há cache de licença válido e o servidor está indisponível.
- Configuração remota tolerante a endpoint ausente, resposta inválida ou falha de rede, usando cache/defaults.
- Sanitização reforçada para erros, eventos de uso, jobs e payloads de cotação antes de envio ao servidor.
- Workflow Windows gera instalador, ZIP de update, launcher e assets estáveis para o repositório de releases.
- ZIP de update validado e assinado quando chaves de assinatura estiverem configuradas.
- Documentação de deploy, backup, update e licenciamento atualizada.
