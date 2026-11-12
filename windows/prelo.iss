; Instalador do Prelo para Windows (Inno Setup 6). Quem o gera é o windows\build.ps1:
;
;   iscc /DVersao=0.3.0 /DOrigem=dist\windows\Prelo /DSaida=dist windows\prelo.iss
;
; Instala só para o usuário atual, sem pedir administrador (como o install.sh no Linux):
;
;   %LOCALAPPDATA%\Programs\Prelo   o app: prelo-app.exe, prelo.exe e os utilitários
;   menu Iniciar                    atalho "Prelo" (e, se pedido, na área de trabalho)
;   "Abrir com"                     PDF, EPUB, AZW3, MOBI e CBZ, sem virar o programa padrão
;   PATH (opcional)                 prelo, prelo-lote, prelo-enviar e prelo-vigiar no terminal
;
; A estante e as preferências ficam em %LOCALAPPDATA%\Prelo; o desinstalador pergunta
; antes de apagá-las.

#if Ver < EncodeVer(6, 3, 0, 0)
  #error Este instalador precisa do Inno Setup 6.3 ou mais novo (https://jrsoftware.org/isinfo.php)
#endif

#ifndef Versao
  #define Versao "0.0.0"
#endif
#ifndef Origem
  #define Origem "..\dist\windows\Prelo"
#endif
#ifndef Saida
  #define Saida "..\dist"
#endif

#define Nome "Prelo"
#define Site "https://github.com/pdrza/prelo"
#define Exe "prelo-app.exe"
#define ProgId "Prelo.Livro"
; o mesmo de prelo.core.system.APP_USER_MODEL_ID: a janela se agrupa com o atalho
#define AppUserModelId "Prelo.Prelo"

[Setup]
AppId={{4D44FE03-6989-4A08-A7F7-17BB172B352B}
AppName={#Nome}
AppVersion={#Versao}
AppVerName={#Nome} {#Versao}
AppPublisher={#Nome}
AppPublisherURL={#Site}
AppSupportURL={#Site}/issues
AppUpdatesURL={#Site}/releases
AppComments=Conversor universal de livros que não quebra o layout
VersionInfoVersion={#Versao}
VersionInfoDescription=Instalador do {#Nome}
DefaultDirName={autopf}\{#Nome}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Saida}
OutputBaseFilename={#Nome}-{#Versao}-windows-x64-setup
SetupIconFile=..\src\prelo\ui\img\icon.ico
UninstallDisplayIcon={app}\{#Exe}
UninstallDisplayName={#Nome}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ChangesAssociations=yes
ChangesEnvironment=yes
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "path"; Description: "Usar os comandos prelo, prelo-lote, prelo-enviar e prelo-vigiar no terminal (adiciona ao PATH)"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; bibliotecas de uma versão anterior não podem sobrar misturadas com as novas
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\scripts"

[Files]
Source: "{#Origem}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#Nome}"; Filename: "{app}\{#Exe}"; AppUserModelID: "{#AppUserModelId}"; Comment: "Converte PDF, EPUB e quadrinhos para Kindle, Kobo e outros, mantendo o layout"
Name: "{autodesktop}\{#Nome}"; Filename: "{app}\{#Exe}"; AppUserModelID: "{#AppUserModelId}"; Tasks: desktopicon

[Registry]
; "Abrir com Prelo": um tipo de arquivo (ProgID) oferecido por cada extensão, sem ser o padrão
Root: HKCU; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: ""; ValueData: "Livro"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#Exe}"",0"
Root: HKCU; Subkey: "Software\Classes\{#ProgId}\shell\open"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#Nome}"
Root: HKCU; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#Exe}"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\.epub\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\.kepub\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\.azw3\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\.azw\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\.mobi\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\.prc\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\.cbz\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty
; o nome e os tipos que aparecem em "Abrir com › Escolher outro aplicativo"
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#Nome}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#Exe}"",0"
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#Exe}"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}\SupportedTypes"; ValueType: string; ValueName: ".epub"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}\SupportedTypes"; ValueType: string; ValueName: ".kepub"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}\SupportedTypes"; ValueType: string; ValueName: ".azw3"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}\SupportedTypes"; ValueType: string; ValueName: ".azw"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}\SupportedTypes"; ValueType: string; ValueName: ".mobi"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}\SupportedTypes"; ValueType: string; ValueName: ".prc"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\{#Exe}\SupportedTypes"; ValueType: string; ValueName: ".cbz"; ValueData: ""

[Run]
Filename: "{app}\{#Exe}"; Description: "{cm:LaunchProgram,{#Nome}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; a tarefa agendada do prelo-vigiar --instalar-servico, se houver
Filename: "{sys}\schtasks.exe"; Parameters: "/End /TN ""Prelo vigiar"""; Flags: runhidden; RunOnceId: "PararVigiar"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""Prelo vigiar"" /F"; Flags: runhidden; RunOnceId: "RemoverVigiar"

[Code]
const
  WebView2Url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  WebView2Id = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function VersaoValida(Raiz: Integer; Chave: String): Boolean;
var
  Versao: String;
begin
  Result := RegQueryStringValue(Raiz, Chave, 'pv', Versao) and (Versao <> '') and (Versao <> '0.0.0.0');
end;

{ A janela do Prelo usa o WebView2 (o motor do Edge). Vem com o Windows 11 e chega ao
  Windows 10 pelo Windows Update; sem ele, o Prelo abre no navegador. }
function TemWebView2: Boolean;
begin
  Result := VersaoValida(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WebView2Id)
    or VersaoValida(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2Id)
    or VersaoValida(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WebView2Id);
end;

function PathDoUsuario: String;
var
  Valor: String;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Valor) then
    Valor := '';
  Result := Valor;
end;

function EstaNoPath(Pasta: String): Boolean;
begin
  Result := Pos(';' + Uppercase(Pasta) + ';', ';' + Uppercase(PathDoUsuario) + ';') > 0;
end;

procedure AdicionarAoPath(Pasta: String);
var
  Caminho: String;
begin
  if EstaNoPath(Pasta) then
    Exit;
  Caminho := PathDoUsuario;
  if (Caminho <> '') and (Copy(Caminho, Length(Caminho), 1) <> ';') then
    Caminho := Caminho + ';';
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Caminho + Pasta);
end;

procedure RemoverDoPath(Pasta: String);
var
  Caminho: String;
  P: Integer;
begin
  Caminho := ';' + PathDoUsuario + ';';
  P := Pos(';' + Uppercase(Pasta) + ';', Uppercase(Caminho));
  if P = 0 then
    Exit;
  Delete(Caminho, P, Length(Pasta) + 1);
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Copy(Caminho, 2, Length(Caminho) - 2));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Codigo: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  if WizardIsTaskSelected('path') then
    AdicionarAoPath(ExpandConstant('{app}'))
  else
    RemoverDoPath(ExpandConstant('{app}'));
  if not TemWebView2 and not WizardSilent then
    if MsgBox('A janela do Prelo usa o Microsoft Edge WebView2 Runtime, que não foi encontrado neste computador. '
      + 'Sem ele, o Prelo abre no navegador.' + #13#10#13#10
      + 'Abrir a página de download da Microsoft agora?', mbConfirmation, MB_YESNO) = IDYES then
      ShellExecAsOriginalUser('open', WebView2Url, '', '', SW_SHOWNORMAL, ewNoWait, Codigo);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dados: String;
begin
  if CurUninstallStep = usUninstall then
    RemoverDoPath(ExpandConstant('{app}'));
  if CurUninstallStep = usPostUninstall then
  begin
    Dados := ExpandConstant('{localappdata}\{#Nome}');
    if DirExists(Dados) and not UninstallSilent then
      if MsgBox('Apagar também a estante do Prelo (capas, preferências e registros)?' + #13#10 + Dados + #13#10#13#10
        + 'Seus livros e os arquivos convertidos ficam onde estão.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Dados, True, True, True);
  end;
end;
