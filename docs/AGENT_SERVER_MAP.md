# AGENT_SERVER_MAP.md

Mapa do repositorio `RomaneioBeta-server` para agentes. Este arquivo fica no repositorio desktop como referencia cruzada porque a escrita direta no repositorio server foi bloqueada pela ferramenta, apesar de o usuario autenticado ter permissao administrativa.

## Repositorio

- Nome: `kaianesteffens/RomaneioBeta-server`
- Branch padrao: `main`
- Papel: API central do Fretio/RomaneioBeta.

## Responsabilidade do server

O server faz:

- validacao de licencas;
- vinculo e limite de maquinas;
- configuracao remota segura por licenca;
- cadastro e consulta de versoes do desktop;
- recebimento de logs sanitizados;
- recebimento de eventos de uso;
- registro e atualizacao de jobs de cotacao;
- normalizacao auxiliar de payloads;
- painel administrativo;
- exportacoes operacionais.

O server nao faz:

- Playwright;
- Chromium;
- login em portais;
- cotacao real em transportadoras;
- leitura de PDF, XML ou DANFE;
- armazenamento de credenciais de transportadoras.

## Stack

- Python 3.12
- FastAPI
- PostgreSQL
- SQLAlchemy 2.x
- Alembic
- Uvicorn
- Docker/Coolify

## Arquivos principais

- `README.md`: visao geral, stack, execucao e docs.
- `docs/ARCHITECTURE.md`: responsabilidades, componentes, banco e relacao com desktop.
- `app/main.py`: instancia FastAPI, `/health`, painel `/admin`, arquivos estaticos e routers.
- `app/settings.py`: configuracoes de ambiente.
- `app/database.py`: engine SQLAlchemy, SessionLocal e dependencia de banco.
- `app/models.py`: modelos/tabelas.
- `app/schemas.py`: contratos Pydantic.
- `app/security.py`: autenticacao admin, normalizacao e hash de erro.
- `app/public_limits.py`: limite em memoria para endpoints publicos.
- `alembic/`: migrations.
- `start.sh`: aplica migrations e inicia API.

## Routers

Registrados em `app/main.py`:

- `app/routers/licenses.py` em `/api/licenses`
- `app/routers/errors.py` em `/api/errors`
- `app/routers/usage.py` em `/api/usage`
- `app/routers/quotations.py` em `/api/quotations`
- `app/routers/version.py` em `/api/version`
- `app/routers/admin.py` em `/api/admin`

Tambem existe:

- `GET /health`
- `GET /admin`
- `/admin/static`

## Banco de dados

Modelos em `app/models.py`:

- `Client`: cliente dono de licencas.
- `License`: chave, status, bloqueio, validade, limite de maquinas e contagem de validacoes.
- `LicenseMachine`: maquinas vinculadas a uma licenca.
- `LicenseSettings`: configuracao remota por licenca.
- `ErrorReport`: erros sanitizados recebidos do desktop.
- `ErrorIssueStatus`: status/resolucao de grupos de erro.
- `AppVersion`: versoes publicadas do desktop.
- `UsageEvent`: eventos de uso/telemetria.
- `QuotationJob`: jobs de cotacao e resultados enviados pelo desktop.

Regra: mudou modelo persistido, criar migration Alembic.

## Licencas

Arquivo:

- `app/routers/licenses.py`

Fluxo:

1. Recebe chave e machine id.
2. Normaliza entrada.
3. Busca licenca.
4. Atualiza contador e ultimo uso da maquina.
5. Bloqueia quando licenca/maquina nao esta permitida.
6. Vincula maquina nova se houver limite.
7. Para configuracao remota, carrega/cria `LicenseSettings` e devolve campos publicos seguros.

Schemas:

- `LicenseValidateRequest`
- `LicenseValidateResponse`
- `PublicLicenseConfigRequest`
- `PublicLicenseConfigResponse`

## Configuracao remota

Arquivos:

- `app/license_settings.py`
- `app/license_settings_service.py`
- `app/models.py`
- `app/schemas.py`
- `app/routers/admin.py`
- `app/routers/licenses.py`

Controla:

- origem padrao;
- fator de cubagem;
- versao minima;
- update obrigatorio;
- permissoes de cotacao, rastreio, NF-e e romaneio;
- transportadoras habilitadas/desabilitadas.

Nao deve incluir credenciais de transportadora.

## Versoes

Arquivos:

- `app/routers/version.py`
- `app/routers/admin.py`
- `app/models.py` (`AppVersion`)

Fluxo:

1. Admin cadastra versao.
2. Endpoint publico devolve a versao ativa mais recente.
3. Desktop consulta essa rota antes de usar fallback de release.

## Erros

Arquivos:

- `app/routers/errors.py`
- `app/error_diagnostics.py`
- `app/security.py`
- `app/models.py` (`ErrorReport`, `ErrorIssueStatus`)

Fluxo:

1. Desktop envia erro ja reduzido.
2. Server sanitiza texto, traceback, contexto e estado do browser.
3. Normaliza modulo, provider, etapa, evento, severidade e origem.
4. Calcula hash.
5. Persiste para consulta no admin.

## Eventos de uso

Arquivos:

- `app/routers/usage.py`
- `app/usage_events.py`
- `app/models.py` (`UsageEvent`)

Fluxo:

1. Desktop envia evento.
2. Server valida tipo permitido e metadados.
3. Se a licenca existir, vincula a cliente/licenca.
4. Persiste evento.

## Jobs de cotacao

Arquivos:

- `app/routers/quotations.py`
- `app/quotations.py`
- `app/quotation_normalizer.py`
- `app/models.py` (`QuotationJob`)

Fluxo:

1. Desktop cria job com payload minimo.
2. Server valida acesso e normaliza auxiliarmente.
3. Server grava status inicial.
4. Desktop executa cotacao localmente.
5. Desktop envia resultado sanitizado.
6. Server atualiza status e resultado.

Regra: job e auditoria/status; execucao real continua no desktop.

## Admin

Arquivo:

- `app/routers/admin.py`

Responsabilidades:

- clientes;
- licencas;
- maquinas;
- configuracao por licenca;
- versoes;
- erros agrupados;
- eventos de uso;
- jobs de cotacao;
- exportacoes CSV/JSON.

Arquivos estaticos:

- `app/static/admin/`

## Limites publicos

Arquivo:

- `app/public_limits.py`

Implementa limite simples em memoria por escopo, IP e licenca quando disponivel. Tambem limita tamanho do corpo das chamadas publicas.

## Onde mexer por problema

- Licenca nao valida: `app/routers/licenses.py`, `app/models.py`, `app/schemas.py`.
- Config remota errada: `app/license_settings.py`, `app/license_settings_service.py`, `app/routers/admin.py`, `app/routers/licenses.py`.
- Versao/update: `app/routers/version.py`, `app/routers/admin.py`, `AppVersion`.
- Logs de erro: `app/routers/errors.py`, `app/error_diagnostics.py`, `ErrorReport`.
- Eventos: `app/routers/usage.py`, `app/usage_events.py`, `UsageEvent`.
- Jobs: `app/routers/quotations.py`, `app/quotations.py`, `app/quotation_normalizer.py`, `QuotationJob`.
- Painel admin: `app/routers/admin.py` e `app/static/admin/`.
- Banco: `app/models.py`, `alembic/versions/`, router/service relacionado.
- Deploy: `Dockerfile`, `docker-compose.yml`, `start.sh`, docs de deploy.

## Prompt recomendado para agentes no server

```txt
Leia AGENTS.md e PROJECT_MAP.md se existirem. Se nao existirem no server, leia docs/AGENT_SERVER_MAP.md no repositorio desktop como referencia.
A tarefa e: <descrever problema>.
Abra primeiro o router da area afetada, depois schema/model/service relacionado.
Nao implemente Playwright no server.
Se alterar modelo persistido, crie migration Alembic.
Mantenha compatibilidade com o desktop.
Explique como testar.
```
