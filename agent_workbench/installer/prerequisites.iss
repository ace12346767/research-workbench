#include "prerequisite_policy.iss"

var
  DependencyPage: TInputOptionWizardPage;

procedure InitializeWizard;
begin
  DependencyPage := CreateInputOptionPage(wpSelectDir, '运行环境',
    'Microsoft Edge WebView2 Runtime',
    '本标准安装包优先使用系统已有的 WebView2。若尚未安装，可使用微软引导程序联网下载并安装。'#13#10#13#10 +
    '无法联网时，请使用完整离线安装包，或先手动安装运行库再重试。', False, False);
  DependencyPage.Add('缺少 WebView2 时，允许联网下载并安装 Microsoft WebView2 Runtime');
  DependencyPage.Values[0] := False;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = DependencyPage.ID) and WebView2Installed;
end;

function DependencyMessage(Action: String; Code: Integer): String;
begin
  if Action = 'net' then
    Result := '未检测到可用的系统 .NET Framework。请确认使用 Windows 10 版本 2004 或更新版本、Windows 11，并修复系统自带的 .NET Framework 后重试。'
  else if Action = 'manual' then
    Result := '缺少 WebView2 Runtime。静默安装不会自动联网，请先安装运行库，或使用完整离线安装包。'
  else if Action = 'consent' then
    Result := '缺少 WebView2 Runtime。请返回“运行环境”页面勾选安装许可，或先手动安装运行库后重试。'
  else if Action = 'restart' then
    Result := 'WebView2 安装要求重启。请先重启 Windows，再重新运行 AgentWorkbench 安装包。'
  else if Action = 'launch' then
    Result := '无法启动 WebView2 安装程序。请检查系统权限或安全软件，解决后点击“重试”。错误码：' + IntToStr(Code)
  else
    Result := 'WebView2 Runtime 安装未完成，应用尚未安装。请检查网络、代理、磁盘空间及权限后点击“重试”；无法联网时可使用完整离线安装包。退出码：' + IntToStr(Code);
end;

function CheckDependencies(SilentMode, Consent: Boolean): String;
var
  Action: String;
  ExitCode, Attempt: Integer;
  Launched, Detected: Boolean;
begin
  Result := '';
  Action := PrerequisiteAction(NetFrameworkInstalled, WebView2Installed, SilentMode, Consent);
  Log('Prerequisite preflight: ' + Action);
  if Action = 'ready' then Exit;
  if Action <> 'install' then begin
    Result := DependencyMessage(Action, 0);
    Log(Result);
    Exit;
  end;
  try
    Launched := RunWebView2Installer(ExitCode);
    Detected := WebView2Installed;
    // Registration may become visible just after the runtime installer exits.
    if Launched and not Detected and (ExitCode = 0) then begin
      for Attempt := 1 to 10 do begin
        Sleep(500);
        Detected := WebView2Installed;
        if Detected then Break;
      end;
    end;
    Action := WebViewInstallResult(Launched, ExitCode, Detected);
    Log('WebView2 installer result: ' + Action + ', exit code ' + IntToStr(ExitCode));
    if Action <> 'ready' then Result := DependencyMessage(Action, ExitCode);
  except
    Result := 'WebView2 安装未完成：' + GetExceptionMessage + #13#10 +
      '请检查网络、磁盘空间、权限和安全软件后重试，或使用完整离线安装包。';
  end;
  // Never schedule a restart or continue copying the app with missing dependencies.
  if Result <> '' then Log(Result);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := CheckDependencies(WizardSilent, DependencyPage.Values[0]);
end;
