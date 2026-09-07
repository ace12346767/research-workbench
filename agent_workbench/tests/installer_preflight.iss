[Setup]
AppName=AgentWorkbench Preflight Test
AppVersion=1
DefaultDirName={tmp}\AWB-Preflight-Test
PrivilegesRequired=lowest
Uninstallable=no
CreateAppDir=no
OutputBaseFilename=preflight-test
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041

[Code]
#include "..\installer\prerequisite_platform.iss"
#include "..\installer\prerequisites.iss"

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    if not SaveStringToFile(ExpandConstant('{param:REPORT}'), 'native preflight passed', False) then
      RaiseException('Cannot write test report');
end;
