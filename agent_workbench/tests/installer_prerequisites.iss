[Setup]
AppName=AgentWorkbench Prerequisite Policy Test
AppVersion=1
DefaultDirName={tmp}\AWB-Prerequisite-Test
PrivilegesRequired=lowest
Uninstallable=no
CreateAppDir=no
OutputBaseFilename=prerequisite-policy-test

[Code]
#include "..\installer\prerequisite_policy.iss"

procedure Expect(Actual, Expected: String);
begin
  if Actual <> Expected then
    RaiseException('Expected ' + Expected + ', got ' + Actual);
end;

function InitializeSetup(): Boolean;
var
  Report: String;
begin
  Report := ExpandConstant('{param:REPORT}');
  Expect(PrerequisiteAction(False, False, False, True), 'net');
  Expect(PrerequisiteAction(False, True, True, False), 'net');
  Expect(PrerequisiteAction(True, True, False, False), 'ready');
  Expect(PrerequisiteAction(True, True, True, False), 'ready');
  Expect(PrerequisiteAction(True, False, True, True), 'manual');
  Expect(PrerequisiteAction(True, False, False, False), 'consent');
  Expect(PrerequisiteAction(True, False, False, True), 'install');
  Expect(WebViewInstallResult(False, 0, False), 'launch');
  Expect(WebViewInstallResult(True, 1, False), 'failed');
  Expect(WebViewInstallResult(True, 0, False), 'missing');
  Expect(WebViewInstallResult(True, 3010, False), 'restart');
  Expect(WebViewInstallResult(True, 3010, True), 'restart');
  Expect(WebViewInstallResult(True, 0, True), 'ready');
  Expect(WebViewInstallResult(True, 1, True), 'ready');
  if not SaveStringToFile(Report, '14 policy checks passed', False) then
    RaiseException('Cannot write test report');
  Result := False;
end;
