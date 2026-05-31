# Atualização do Desktop

O updater fica em `app/updater.py`. Ele descobre versões novas, baixa o ZIP de update, valida a estrutura e aplica a troca de arquivos.

## Descoberta de versão

Preferencialmente o app consulta:

```toml
[fretio]
version_api_url = "https://api.exemplo.com/api/version/latest"
```

Resposta esperada:

```json
{
  "latest_version": "2.31.0",
  "download_url": "https://github.com/owner/releases/download/v2.31.0/Fretio-Update-2.31.0.zip",
  "mandatory": false,
  "release_notes": "Correções e melhorias."
}
```

Se `latest_version` for maior que a versão local, o app mostra o update. Se `mandatory` for `true`, o uso fica bloqueado até atualizar ou fechar o app.

## Fallback GitHub Releases

Se `version_api_url` estiver ausente, indisponível ou retornar resposta inválida, o updater usa:

```toml
[fretio]
github_repo = "owner/releases-repo"
github_repo_aliases = ["owner/releases-repo-antigo"]
```

O `download_url` do servidor pode continuar apontando para assets publicados no GitHub Releases. O servidor só centraliza a decisão de qual versão está ativa.

## Aplicação do update

O pacote de update deve conter:

- `Fretio.exe` ou `FreteBot.exe`
- `version.txt` ou `_internal/version.txt`

O updater rejeita ZIP com path traversal, caminho absoluto ou estrutura inválida. Quando existir assinatura, `update_security.py` verifica o asset `.sig`.

## Política de versão mínima

A configuração remota pode retornar:

```json
{
  "min_app_version": "2.30.0",
  "force_update": true
}
```

Com `force_update=true`, versões abaixo da mínima são bloqueadas. Com `force_update=false`, o app apenas avisa e permite continuar.

## Publicação

O workflow de release gera instalador e ZIP de update. Para dependências, editar `installer/requirements.in` e regenerar `installer/requirements-lock.txt` em Windows antes de publicar.

Não publicar tokens, `CONFIG.toml`, chaves de licença ou credenciais nos assets.
