[Setup]
AppName=AgentWorkbench Offline Payload Verification
AppVersion=1
DefaultDirName={tmp}\AWB-Offline-Payload-Test
PrivilegesRequired=lowest
Uninstallable=no
CreateAppDir=no
OutputBaseFilename=offline-payload-test

[Files]
Source: "..\.tmp\prerequisites\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Flags: dontcopy nocompression

[Code]
function InitializeSetup(): Boolean;
var
  Actual, Expected: String;
begin
  ExtractTemporaryFile('MicrosoftEdgeWebView2RuntimeInstallerX64.exe');
  Actual := GetSHA256OfFile(ExpandConstant('{tmp}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'));
  Expected := ExpandConstant('{param:EXPECTED}');
  if (Expected = '') or (CompareText(Actual, Expected) <> 0) then
    RaiseException('Offline payload hash mismatch');
  if not SaveStringToFile(ExpandConstant('{param:REPORT}'), Actual, False) then
    RaiseException('Cannot write payload test report');
  Result := False;
end;
