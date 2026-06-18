# Release segura

Checklist operacional para publicar uma versão liberável sem quebrar instalações antigas.

## Escopo da versão 2.32

- Desktop usa server quando `license_api_url`, `license_config_api_url`, `version_api_url`, `error_api_url`, `usage_api_url` e endpoints de cotação estiverem configurados.
- Desktop antigo ou instalação sem server continua validando pelo Gist legado via `license_url`.
- Server offline não bloqueia uso quando existe cache de licença válido dentro do período de graça.
- Configuração remota ausente, inválida ou offline não quebra a inicialização nem a cotação local.

## Pré-release

- Server: confirmar `alembic upgrade head` em banco limpo e banco com versão anterior.
- Server: confirmar que `start.sh` está executável e roda `alembic upgrade head` antes do Uvicorn.
- Server: revisar `README.md`, `docs/DEPLOY_COOLIFY.md`, `docs/BACKUP.md` e `.env.example`.
- Server: criar backup PostgreSQL antes de aplicar migrations em produção.
- Desktop: confirmar `app/version.txt` com a versão da release.
- Desktop: atualizar `CHANGELOG.md`.
- Desktop: confirmar que `CONFIG.toml`, `.env`, tokens, dumps e backups não aparecem no diff.
- Desktop: confirmar que exemplos e testes usam documentos fictícios ou sanitizados.

## Validações locais

```bash
cd RomaneioBeta-server
env ADMIN_TOKEN=test-admin-token DATABASE_URL=sqlite+pysqlite:////tmp/romaneio-release-server.db PYTHONPATH=. pytest
alembic upgrade head
sh -n start.sh

cd ../RomaneioBeta
python -m pytest
python installer/validate_update_zip.py installer/installer/Fretio-Update-2.32.zip
```

O `validate_update_zip.py` só se aplica depois que o workflow ou build Windows gerar o ZIP.

Se testes baseados em `fastapi.testclient.TestClient` travarem no ambiente local, confirme com
um `GET /health` mínimo antes de tratar como regressão de `usage_events`. Em 2026-05-31, o
travamento reproduziu no primeiro request do `TestClient`, sem falha de assert e sem entrar
na lógica específica de eventos de uso; as validações de migrations e os testes unitários sem
`TestClient` continuaram executando normalmente.

## Build Windows

O workflow `Build and Release Fretio` é manual. Depois do merge no `master`, abra **Actions → Build and Release Fretio → Run workflow**, informe uma versão nova em `version` (`X.Y` ou `X.Y.Z`, por exemplo `2.34`) e mantenha `publish_release=true` para release oficial de cliente.

Configuração obrigatória para release oficial de cliente:

- Secret `UPDATE_SIGNING_PRIVATE_KEY_B64`
- Variable `UPDATE_PUBLIC_KEY_B64`
- Variable `ALLOW_UNSIGNED_DEV_RELEASE=false`

As releases são publicadas no próprio repositório `kaianesteffens/RomaneioBeta`
(que é público), então o `GITHUB_TOKEN` automático do workflow já basta — **não é
mais necessário um `RELEASES_TOKEN`**. Só defina `RELEASE_REPO` (e um `RELEASES_TOKEN`
com acesso a ele) se quiser publicar em um repositório diferente do atual.

Com `publish_release=true`, o workflow valida que `version` é maior que a maior tag já publicada no repositório, atualiza `app/version.txt` antes do build, assina os ZIPs de update, publica a GitHub Release `v<version>` e falha se faltar chave de assinatura ou qualquer artefato obrigatório.

Para build interno sem publicação externa, rode manualmente com `publish_release=false`. Se as chaves de assinatura estiverem ausentes, esse modo só deve ser usado com `ALLOW_UNSIGNED_DEV_RELEASE=true`; ele gera apenas artefatos internos do workflow e não publica ZIP sem `.sig` no repositório de releases.

O workflow deve gerar, para a versão informada:

- `Fretio-Setup-<version>.exe`
- `Fretio-Update-<version>.zip`
- `Fretio-Update-<version>.zip.sig` em release oficial assinada
- `Romaneio.exe`
- assets `*-latest.*`
- `installer/repository-assets/latest.json` em release oficial

## Publicação

- Conferir que a GitHub Release `v2.32` existe em `kaianesteffens/RomaneioBeta`.
- Conferir anexos: instalador, ZIP de update, assinatura do ZIP, launcher e aliases `latest`.
- Conferir que `latest.json`, se versionado/gerado, aponta para `2.32` e para `kaianesteffens/RomaneioBeta`.
- No server, cadastrar ou ativar `/api/admin/versions` com:
  - `version`: `2.32`
  - `download_url`: URL do asset `Fretio-Update-2.32.zip` ou `Fretio-Update-latest.zip`
  - `mandatory`: `false` inicialmente
  - `active`: `true`
  - `release_notes`: resumo do changelog

## Testes pós-release

- Instalação nova em Windows limpo.
- Atualização a partir da versão anterior instalada.
- Validação de licença via server.
- Validação de licença via Gist em instalação sem `license_api_url`.
- Inicialização com server offline e cache válido.
- Inicialização com config remota ausente.
- Cotação com pelo menos uma transportadora habilitada.
- Envio de erro fake para `/api/errors`.
- Evento de uso best-effort para `/api/usage/events`.
- Painel admin: login por `ADMIN_TOKEN`, dashboard, licenças, erros, jobs e versões.

## Rollback

1. Desativar a versão problemática em `/api/admin/versions` ou marcar a versão anterior como ativa.
2. Se necessário, editar a release anterior no GitHub como `latest`.
3. Reverter os assets `*-latest.*` no repositório de releases para os artefatos da versão anterior.
4. Manter `mandatory=false` até validar que clientes conseguem voltar ou permanecer na versão anterior.
5. Se o problema veio de migration, restaurar backup PostgreSQL em banco separado, validar dados e só então trocar `DATABASE_URL`/serviço.
6. Rotacionar `ADMIN_TOKEN`, tokens GitHub e chaves se qualquer segredo tiver sido exposto.

Não apagar a release problemática até coletar logs e confirmar que nenhum cliente ainda depende dos assets dela.
