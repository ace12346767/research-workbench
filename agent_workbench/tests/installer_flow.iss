[Setup]
AppName=AgentWorkbench Prerequisite Flow Test
AppVersion=1
DefaultDirName={tmp}\AWB-Flow-Test
PrivilegesRequired=lowest
Uninstallable=no
CreateAppDir=no
OutputBaseFilename=flow-test

[Code]
var
  FakeNet, FakeWeb, FakeLaunched, FakeDetected, FakeThrow: Boolean;
  FakeCode, LaunchCount: Integer;

function NetFrameworkInstalled: Boolean;
begin
  Result := FakeNet;
end;

function WebView2Installed: Boolean;
begin
  Result := FakeWeb;
end;

function RunWebView2Installer(var ExitCode: Integer): Boolean;
begin
  LaunchCount := LaunchCount + 1;
  if FakeThrow then RaiseException('test extraction failure');
  FakeWeb := FakeDetected;
  ExitCode := FakeCode;
  Result := FakeLaunched;
end;

#include "..\installer\prerequisites.iss"

procedure Expect(Condition: Boolean);
begin
  if not Condition then RaiseException('Prerequisite flow assertion failed');
end;

function InitializeSetup(): Boolean;
begin
  FakeNet := False;
  Expect(CheckDependencies(False, True) <> '');
  Expect(LaunchCount = 0);
  FakeNet := True;
  Expect(CheckDependencies(True, True) <> '');
  Expect(CheckDependencies(False, False) <> '');
  Expect(LaunchCount = 0);
  FakeWeb := True;
  Expect(CheckDependencies(True, False) = '');
  Expect(LaunchCount = 0);
  FakeWeb := False;
  FakeLaunched := False;
  FakeCode := 5;
  Expect(CheckDependencies(False, True) <> '');
  FakeLaunched := True;
  FakeCode := 1;
  Expect(CheckDependencies(False, True) <> '');
  FakeCode := 0;
  Expect(CheckDependencies(False, True) <> '');
  FakeCode := 3010;
  Expect(CheckDependencies(False, True) <> '');
  FakeCode := 0;
  FakeThrow := True;
  Expect(CheckDependencies(False, True) <> '');
  FakeThrow := False;
  FakeDetected := True;
  Expect(CheckDependencies(False, True) = '');
  Expect(LaunchCount = 6);
  Expect(CheckDependencies(True, False) = '');
  Expect(LaunchCount = 6);
  if not SaveStringToFile(ExpandConstant('{param:REPORT}'), '16 flow checks passed', False) then
    RaiseException('Cannot write test report');
  Result := False;
end;
