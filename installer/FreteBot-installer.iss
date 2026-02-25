; FreteBot — Inno Setup Installer Script
; Gera: FreteBot-Setup.exe
; Requisito: Inno Setup 6+ (https://jrsoftware.org/isdl.php)

#ifndef MyAppName
  #define MyAppName      "Romaneio Beta"
#endif
#ifndef MyAppVersion
  #define MyAppVersion   "1.0"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "Darlu"
#endif
#ifndef MyAppExeName
  #define MyAppExeName   "FreteBot.exe"
#endif
#ifndef MyAppURL
  #define MyAppURL       ""
#endif
#ifndef MyOutputBaseFilename
  #define MyOutputBaseFilename "Romaneio-Beta-Setup"
#endif
#ifndef MySetupIconFile
  #define MySetupIconFile AddBackslash(SourcePath) + "assets\romaneio.ico"
#endif

; Caminho relativo a partir deste .iss
; Após PyInstaller: dist\FreteBot\ contém FreteBot.exe + deps
#define DistDir        AddBackslash(SourcePath) + "dist\FreteBot"

[Setup]
AppId={{A3F7B2D1-8E4C-4F9A-B5D6-1C2E3F4A5B6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
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
; Copia toda a pasta dist\FreteBot\ para {app}
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; CONFIG.toml real (com credenciais) → %APPDATA%\FreteBot\ (sempre sobrescreve)
Source: "{#DistDir}\_internal\CONFIG.toml"; DestDir: "{userappdata}\FreteBot"; DestName: "CONFIG.toml"; Flags: ignoreversion

[Dirs]
Name: "{userappdata}\FreteBot"; Flags: uninsneveruninstall
Name: "{userappdata}\FreteBot\cache"

[Icons]
; Menu Iniciar
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Cotação automática de fretes"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"

; Área de Trabalho (opcional)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "Cotação automática de fretes"

[Run]
; Executar após instalação
Filename: "{app}\{#MyAppExeName}"; Description: "Executar {#MyAppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\FreteBot\cache"

[Code]
// Verificar se já existe CONFIG.toml do usuário e não sobrescrever
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
