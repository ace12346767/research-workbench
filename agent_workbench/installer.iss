#define AppName "AgentWorkbench"
#define AppVersion "0.4.13"
#define AppIconName "AgentWorkbench-Mobius-0.4.5.ico"
#define AppExeName "AgentWorkbench.exe"

[Setup]
AppId={{CB4A75A7-F370-4D73-BAF7-1A493D2B3E35}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=AgentWorkbench
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=output
OutputBaseFilename=AgentWorkbench-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
MinVersion=10.0.19041
WizardStyle=modern
SetupIconFile=assets\AgentWorkbench.ico
UninstallDisplayIcon={app}\{#AppIconName}
CloseApplications=yes
RestartApplications=no

[Files]
Source: ".tmp\prerequisites\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy
Source: "dist\AgentWorkbench\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\AgentWorkbench.ico"; DestDir: "{app}"; DestName: "{#AppIconName}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppIconName}"; AppUserModelID: "AgentWorkbench.Desktop"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppIconName}"; AppUserModelID: "AgentWorkbench.Desktop"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent; Check: WebView2Installed and NetFrameworkInstalled

[Code]
procedure SHChangeNotify(EventId: Integer; Flags: Cardinal; Item1: string; Item2: Integer);
  external 'SHChangeNotify@shell32.dll stdcall';

#include "installer\prerequisite_platform.iss"
#include "installer\prerequisites.iss"

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then
  begin
    SHChangeNotify($00002000, $0005, ExpandConstant('{autodesktop}\{#AppName}.lnk'), 0);
    SHChangeNotify($00002000, $0005, ExpandConstant('{group}\{#AppName}.lnk'), 0);
  end;
end;
