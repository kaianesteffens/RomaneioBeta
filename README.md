Fretio / Fretio
Aplicativo desktop Windows para operacao de romaneios, cotacao de frete e rastreio de entregas. O projeto extrai dados de pedidos em PDF e NF-e em XML/DANFE, consulta transportadoras em paralelo e centraliza a operacao por empresa com configuracoes separadas.
O que o sistema faz
Extrai pedidos de PDF e monta o romaneio para consulta e copia.
Calcula frete a partir do romaneio colado na interface.
Cota frete de fornecedores com preenchimento manual de volumes, pesos e valor da mercadoria.
Rastreia entregas a partir de XMLs de NF-e e gera screenshots quando o fluxo exige evidencia.
Trabalha com multiplas empresas no mesmo computador, cada uma com seu proprio `CONFIG.toml`.
Valida licenca na inicializacao e suporta atualizacao automatica via GitHub Releases.
Transportadoras atuais
Os providers implementados no repositorio ficam em `app\\Fretio\\src\\Fretio\\providers\\` e hoje cobrem:
Braspress
TRD
AGEX
Eucatur
Rodonaves
Alfa
Coopex
Stack principal
Python 3.12
PySide6 para a interface desktop
Playwright + Chromium para automacao dos portais
pdfplumber para leitura de PDFs
PyInstaller + Inno Setup para empacotamento e instalador Windows
Fluxo do aplicativo
Na abertura, o usuario seleciona a empresa com a qual vai operar.
Em Configuracoes, define credenciais, UFs atendidas e parametros da empresa.
Na tela inicial, escolhe um dos modulos:
Romaneio: extrair pedidos de PDF
Calcular Frete: cotar a partir do romaneio
Frete Fornecedores: cotar com dados manuais
Rastreio: importar XML(s) de NF-e e acompanhar entrega
Configuracao
O projeto usa configuracao por empresa em:
```text
%APPDATA%\\Fretio\\empresas\\<nome-da-empresa>\\CONFIG.toml
```
O arquivo-base para referencia esta em:
```text
app\\CONFIG.example.toml
```
Principais secoes:
`\[Fretio]`: cubagem, cache, repositorio de releases, licenciamento e reporte de erros
`\[romaneio]`: CEP de origem padrao
`\[transportadoras.<nome>]`: habilitacao, credenciais, `headless`, UFs atendidas e campos especificos de cada integracao
> `CONFIG.toml`, credenciais e licencas sao dados locais e nao devem ser versionados.
Executando em desenvolvimento
Recomendado em Windows 10/11 com Python 3.12.
```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install -r installer\\requirements.txt
python -m pip install pyinstaller
python -m playwright install chromium
python app\\romaneio\_app.py
```
Observacoes:
O aplicativo solicita uma chave de licenca na primeira execucao.
O Chromium do Playwright precisa estar instalado para cotacao e rastreio.
Em ambiente empacotado, a versao usada pelo app vem de `app\\version.txt`.
Build do instalador
O fluxo de build fica em `installer\\`.
Build local
```cmd
cd installer
build.bat
```
O processo gera, entre outros artefatos:
`installer\\installer\\Romaneio-Beta-Setup-<versao>.exe`
`installer\\installer\\Fretio-Update-<versao>.zip`
`installer\\installer\\Romaneio.exe`
GitHub Actions
O workflow `.github\\workflows\\build-release.yml` roda manualmente e:
executa o build Windows;
gera os artefatos do instalador e do pacote de update;
faz o bump de `app\\version.txt`;
publica a release no repo configurado em `RELEASE_REPO`, com aliases estaveis de update/instalador;
mantem uma copia atualizada em `installer\\repository-assets\\` dentro do repositorio privado.
Estrutura do repositorio
Caminho	Responsabilidade
`app\\romaneio\_app.py`	Janela principal PySide6 e fluxo entre modulos
`app\\cotacao\_transportadoras.py`	Orquestracao das cotacoes e sessoes por transportadora
`app\\extrator\_pedidos.py`	Extracao de pedidos em PDF para romaneio
`app\\extrator\_nfe.py`	Leitura de XML/DANFE de NF-e
`app\\rastreamento.py`	Rastreio de entregas e captura de screenshots
`app\\updater.py`	Atualizacao automatica via GitHub Releases
`app\\license.py` / `app\\license\_manager.py`	Validacao e persistencia de licenca
`app\\Fretio\\src\\Fretio\\providers\\`	Integracoes com transportadoras
`installer\\`	Empacotamento PyInstaller e instalador Inno Setup
Notas para manutencao
O app usa threads para manter a UI responsiva; Playwright nao deve rodar na thread principal do Qt.
Novas transportadoras devem ser implementadas em `providers\\` e registradas em `app\\cotacao\_transportadoras.py`.
O projeto foi pensado para Windows e depende de caminhos e rotinas especificas desse ambiente.
