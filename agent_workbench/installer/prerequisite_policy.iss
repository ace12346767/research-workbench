function PrerequisiteAction(NetReady, WebReady, SilentMode, Consent: Boolean): String;
begin
  if not NetReady then Result := 'net'
  else if WebReady then Result := 'ready'
  else if SilentMode then Result := 'manual'
  else if not Consent then Result := 'consent'
  else Result := 'install';
end;

function WebViewInstallResult(Launched: Boolean; ExitCode: Integer; Detected: Boolean): String;
begin
  if not Launched then Result := 'launch'
  else if ExitCode = 3010 then Result := 'restart'
  else if Detected then Result := 'ready'
  else if ExitCode <> 0 then Result := 'failed'
  else Result := 'missing';
end;
