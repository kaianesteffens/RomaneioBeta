; Fretio — Inno Setup Installer Script
; Gera: Fretio-Setup.exe
; Requisito: Inno Setup 6+ (https://jrsoftware.org/isdl.php)

#ifndef MyAppName
  #define MyAppName      "Fretio"
#endif
#ifndef MyAppVersion
  #define MyAppVersion   "1.0"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "Darlu"
#endif
#ifndef MyAppExeName
  #define MyAppExeName   "Fretio.exe"
#endif
#ifndef MyAppURL
  #define MyAppURL       ""
#endif
#ifndef MyOutputBaseFilename
  #define MyOutputBaseFilename "Fretio-Setup"
#endif
#ifndef MySetupIconFile
  #define MySetupIconFile AddBackslash(SourcePath) + "assets\romaneio.ico"
#endif

; Caminho relativo a partir deste .iss
; Após PyInstaller: dist\Fretio\ contém Fretio.exe + deps
#define DistDir        AddBackslash(SourcePath) + "dist\Fretio"

[Setup]
AppId={{A3F7B2D1-8E4C-4F9A-B5D6-1C2E3F4A5B6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
UsePreviousAppDir=yes
DefaultGroupName={#MyAppName}
OutputDir={#AddBackslash(SourcePath)}installer
OutputBaseFilename={#MyOutputBaseFilename}
SetupIconFile={#MySetupIconFile}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
LicenseFile=
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
MinVersion=10.0

[Languages]
Name: "portuguesebrazil"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

[Files]
; Copia toda a pasta dist\Fretio\ para {app}
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Bootstrapper do Microsoft Edge WebView2 Runtime (Evergreen, per-user).
; A interface roda em WebView2. No Windows 11 o runtime já vem instalado; em
; Windows 10/imagens corporativas pode faltar. O build deve baixar
; installer\MicrosoftEdgeWebview2Setup.exe (https://go.microsoft.com/fwlink/p/?LinkId=2124703);
; se ausente, esta linha é ignorada e o passo de instalação não roda.
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist

; Nenhum CONFIG.toml é instalado no %APPDATA%. O app cria a config por empresa no
; primeiro uso, com as URLs padrão (sem nenhuma credencial do desenvolvedor).

[Dirs]
Name: "{userappdata}\\Fretio"; Flags: uninsneveruninstall
Name: "{userappdata}\\Fretio\\cache"

[Icons]
; Menu Iniciar
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Cotação automática de fretes"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"

; Área de Trabalho (opcional)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "Cotação automática de fretes"

[Run]
; Instala o WebView2 Runtime se ausente (a interface depende dele)
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; Check: WebView2Ausente; Flags: waituntilterminated skipifdoesntexist; StatusMsg: "Instalando o componente WebView2..."

; Executar após instalação
Filename: "{app}\{#MyAppExeName}"; Description: "Executar {#MyAppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[InstallDelete]
; Modo "Substituir": remove a instalação atual antes de copiar os novos arquivos
Type: filesandordirs; Name: "{app}"; Check: DeveSubstituirInstalacao

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\\Fretio\\cache"

[Code]
var
  _substituirInstalacao: Boolean;

function DeveSubstituirInstalacao(): Boolean;
begin
  Result := _substituirInstalacao;
end;

function WebView2Ausente(): Boolean;
var
  _v: String;
  _clients: String;
begin
  _clients := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := True;
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', _v) and (_v <> '') then
    Result := False
  else if RegQueryStringValue(HKLM, _clients, 'pv', _v) and (_v <> '') then
    Result := False
  else if RegQueryStringValue(HKCU, _clients, 'pv', _v) and (_v <> '') then
    Result := False;
end;

function _existeInstalacaoAtual(): Boolean;
begin
  Result := FileExists(WizardDirValue() + '\{#MyAppExeName}');
end;

procedure InitializeWizard();
var
  _resp: Integer;
  _msg: string;
begin
  _substituirInstalacao := False;

  if _existeInstalacaoAtual() then
  begin
    _msg :=
      'Já existe uma instalação do Fretio neste computador.' + #13#10 + #13#10 +
      'Sim: Atualizar (recomendado).' + #13#10 +
      'Não: Substituir (instalação limpa).' + #13#10 +
      'Cancelar: Sair da instalação.';

    _resp := SuppressibleMsgBox(_msg, mbConfirmation, MB_YESNOCANCEL, IDYES);

    if _resp = IDCANCEL then
      Abort
    else if _resp = IDNO then
      _substituirInstalacao := True;
  end;
end;
