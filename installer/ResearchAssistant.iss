; Windows installer for Research Assistant (Inno Setup 6). Built by .github/workflows/release.yml for every release:
;   ISCC.exe /DMyAppVersion=1.2.0 installer\ResearchAssistant.iss
; Installs the app's source files for the current user (no administrator rights needed). On first start,
; "Start Research Assistant.bat" installs Python and the libraries inside the app folder, and the app's setup page
; downloads the AI engine and a model that suits the computer.

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
Filename: "{app}\Start Research Assistant.bat"; WorkingDir: "{app}"; Description: "Start Research Assistant now (the first start installs Python, about 2 minutes)"; Flags: postinstall nowait shellexec skipifsilent

[UninstallDelete]
; what the app installed by itself; your papers index (data) and AI models (models) are kept - delete the folder to remove them
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\tools\uv"
Type: filesandordirs; Name: "{app}\tools\python"
Type: filesandordirs; Name: "{app}\tools\uv-cache"
Type: filesandordirs; Name: "{app}\engine"
Type: filesandordirs; Name: "{app}\__pycache__"
