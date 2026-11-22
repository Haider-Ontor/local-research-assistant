; Windows installer for Research Assistant (Inno Setup 6). Built by .github/workflows/release.yml for every release:
;   ISCC.exe /DMyAppVersion=1.2.2 installer\ResearchAssistant.iss
; Nothing has to be installed on the computer first. For the current user (no administrator rights), it:
;   1. copies the app, including the Microsoft C++ runtime the AI engine needs (runtime\, added by the workflow);
;   2. installs Python and the libraries inside the app folder (tools\install.ps1);
;   3. downloads the AI engine build that suits the computer's graphics card (start.py --install-engine).
; If steps 2-3 can't finish (no internet), the app finishes them on first start. The app's setup page then asks for
; the papers folder and downloads the AI model it suggests for this computer.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{8E3F2C1A-5B7D-4E9A-9C2B-3D4F5A6B7C8D}
AppName=Research Assistant
AppVersion={#MyAppVersion}
AppVerName=Research Assistant {#MyAppVersion}
AppPublisher=Haider-Ontor
AppPublisherURL=https://github.com/Haider-Ontor/local-research-assistant
AppSupportURL=https://github.com/Haider-Ontor/local-research-assistant/issues
AppUpdatesURL=https://github.com/Haider-Ontor/local-research-assistant/releases
DefaultDirName={localappdata}\ResearchAssistant
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\LICENSE
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\icon.ico
UninstallDisplayName=Research Assistant
OutputDir=..\dist
OutputBaseFilename=ResearchAssistant-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Messages]
SelectDirDesc=AI models are large (1-20 GB) and are saved inside this folder. Choose a drive with space, and a folder that OneDrive doesn't sync.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "..\*"; DestDir: "{app}"; Excludes: "\.git,\.github,\dist,\installer,\data,\engine,\models,\.venv,\tools\uv,\tools\python,\tools\uv-cache,\local_settings.py,__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\Research Assistant"; Filename: "{app}\Start Research Assistant.bat"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Comment: "Chat with your research papers"
Name: "{userdesktop}\Research Assistant"; Filename: "{app}\Start Research Assistant.bat"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Comment: "Chat with your research papers"; Tasks: desktopicon

[Run]
Filename: "{app}\Start Research Assistant.bat"; WorkingDir: "{app}"; Description: "Start Research Assistant now"; Flags: postinstall nowait shellexec skipifsilent

[UninstallDelete]
; what the app installed by itself; your papers index (data) and AI models (models) are kept - delete the folder to remove them
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\tools\uv"
Type: filesandordirs; Name: "{app}\tools\python"
Type: filesandordirs; Name: "{app}\tools\uv-cache"
Type: filesandordirs; Name: "{app}\engine"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\install-log.txt"

[Code]
var
  LogFile: String;

{ Runs a command line hidden, appending its output to install-log.txt, while the wizard shows Msg. }
function RunHidden(const Msg, Cmd: String): Boolean;
var
  Rc: Integer;
begin
  WizardForm.StatusLabel.Caption := Msg;
  WizardForm.FilenameLabel.Caption := 'Details: ' + LogFile;
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    { ExecAsOriginalUser, not Exec: children of Exec inherit Setup's RedirectionGuard mitigation, which stops uv from
      creating its Python folder link ("untrusted mount point", error 448). Setup runs as this user anyway. }
    Result := ExecAsOriginalUser(ExpandConstant('{cmd}'), '/c "' + Cmd + ' >> "' + LogFile + '" 2>&1"',
                                 ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Rc) and (Rc = 0);
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  App, Ps: String;
  Ok: Boolean;
begin
  if CurStep <> ssPostInstall then
    exit;
  App := ExpandConstant('{app}');
  LogFile := App + '\install-log.txt';
  Ps := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  Ok := RunHidden('Installing Python and the app''s libraries into the app folder (about 400 MB, a few minutes)...',
                  '"' + Ps + '" -NoProfile -ExecutionPolicy Bypass -File "' + App + '\tools\install.ps1" -NoShortcut');
  if Ok then
    Ok := RunHidden('Downloading the AI engine that suits this computer''s graphics card (a few minutes)...',
                    '"' + App + '\.venv\Scripts\python.exe" "' + App + '\start.py" --install-engine');
  if not Ok then
    SuppressibleMsgBox('Research Assistant is installed, but part of its setup needs the internet and didn''t finish ' +
                       '(details: ' + LogFile + '). It finishes by itself the first time you start the app.',
                       mbInformation, MB_OK, IDOK);
end;
