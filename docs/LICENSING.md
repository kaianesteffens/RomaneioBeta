# Licenciamento e Configuração Remota

O fluxo principal usa o RomaneioBeta-server. O fallback por Gist existe apenas para compatibilidade antiga.

## `license_api_url`

`license_api_url` aponta para o endpoint público de validação:

```toml
[fretio]
license_api_url = "https://api.exemplo.com/api/licenses/validate"
```

Na inicialização, o desktop envia somente:

```json
{
  "key": "FBOT-XXXX-XXXX-XXXX-XXXX",
  "machine_id": "hash-da-maquina"
}
```

Resposta esperada:

```json
{
  "valid": true,
  "owner": "Cliente Exemplo",
  "message": "Licença válida.",
  "blocked": false,
  "expires": "2026-12-31"
}
```

Se o servidor estiver fora, o app pode usar cache local por período de graça, desde que exista validação recente. Licenças bloqueadas também são persistidas em cache para impedir uso offline indevido.

## Configuração remota

Depois de validar a licença, o app busca configuração segura em:

```toml
[fretio]
license_config_api_url = "https://api.exemplo.com/api/licenses/config"
```

Se `license_config_api_url` não estiver configurado, o app deriva a URL a partir de `license_api_url`, trocando `/validate` por `/config`.

Payload:

```json
{
  "key": "FBOT-XXXX-XXXX-XXXX-XXXX",
  "machine_id": "hash-da-maquina"
}
```

Resposta:

```json
{
  "valid": true,
  "message": "Licença válida.",
  "license": {
    "owner": "Cliente Exemplo",
    "expires": "2026-12-31",
    "blocked": false
  },
  "config": {
    "cep_origem": "01001000",
    "fator_cubagem": 6000,
    "min_app_version": "2.30.0",
    "force_update": false,
    "allow_cotacao": true,
    "allow_rastreio": true,
    "allow_nfe": true,
    "allow_romaneio": true,
    "carriers_enabled": {
      "braspress": true,
      "trd": true,
      "agex": false
    }
  }
}
```

Campos aplicados no desktop:

- `allow_cotacao`, `allow_rastreio`, `allow_nfe`, `allow_romaneio`: bloqueiam módulos na UI.
- `carriers_enabled`: desabilita transportadoras sem tratar como erro técnico.
- `cep_origem`: override seguro de CEP de origem quando válido.
- `fator_cubagem`: override seguro do fator de cubagem.
- `min_app_version` e `force_update`: política de versão mínima.

## Dados proibidos

Configuração remota não é lugar para credenciais. Não enviar ou armazenar no servidor:

- Senhas de transportadoras.
- Login, usuário, cookie, token, bearer ou authorization.
- CNPJ/CPF completos usados em portais.
- HTML bruto, screenshot, XML, PDF ou traceback.
- `ADMIN_TOKEN`, `DATABASE_URL`, token GitHub ou secrets.

O `CONFIG.toml` local continua sendo a fonte das credenciais de transportadoras.

## Cache local

O desktop salva a última configuração remota válida em cache local. Em falha de rede, usa esse cache; se não houver cache, usa defaults seguros:

- módulos liberados;
- transportadoras liberadas;
- sem override de CEP;
- sem override de fator;
- sem versão mínima.

O cache é sanitizado antes de gravar e remove chaves sensíveis.
