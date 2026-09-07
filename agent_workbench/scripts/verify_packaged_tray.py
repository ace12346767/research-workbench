"""Check the distributed desktop's close/hide choices using isolated local data."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import httpx

from agent_workbench import __version__
from agent_workbench.app import find_available_port
from agent_workbench.config import AppConfig, ConfigRepository
from agent_workbench.desktop_tray import ClosePreference


def main():
    project = Path(__file__).resolve().parents[1]
    root = Path(tempfile.mkdtemp(prefix='packaged-tray-', dir=project / '.tmp'))
    ConfigRepository(root / 'config.json').save(AppConfig())
    preference = ClosePreference(root / 'desktop-preferences.json')
    port = find_available_port()
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

    def wait(check):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = check()
            if result:
                return result
            if child.poll() is not None:
                raise RuntimeError('Packaged desktop exited unexpectedly')
            time.sleep(.1)
        raise TimeoutError('Packaged tray check timed out')

    def windows(parent=None):
        result = []
        def collect(handle, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            if pid.value == child.pid:
                title = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(handle, title, len(title))
                result.append((handle, title.value))
            return True
        callback = callback_type(collect)
        if parent:
            user32.EnumChildWindows(parent, callback, 0)
        else:
            user32.EnumWindows(callback, 0)
        return result

    report = {'status':'failed', 'data_dir':str(root), 'checks':[]}
    environment = dict(os.environ, TEMP=str(root), TMP=str(root))
    with (root / 'process.log').open('w') as log:
        child = subprocess.Popen([str(project / 'dist/AgentWorkbench/AgentWorkbench.exe'),
                                  '--data-dir', str(root), '--port', str(port)],
                                 cwd=root, env=environment, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=2, trust_env=False) as client:
                def health():
                    try:
                        return client.get('/health/live').status_code == 200
                    except httpx.HTTPError:
                        return False
                wait(health)
                assert client.get('/openapi.json').json()['info']['version'] == __version__
                main_window = wait(lambda: next((h for h, title in windows() if title == 'AgentWorkbench' and user32.IsWindowVisible(h)), None))
                # Close as soon as visible: handlers must be installed before showing.
                user32.PostMessageW(main_window, 0x0010, 0, 0)
                dialog = wait(lambda: next((h for h, title in windows() if title == '关闭 AgentWorkbench' and user32.IsWindowVisible(h)), None))
                hide = wait(lambda: next((h for h, title in windows(dialog) if title == '隐藏到托盘'), None))
                user32.PostMessageW(hide, 0x00F5, 0, 0)
                wait(lambda: not user32.IsWindowVisible(main_window))
                assert preference.load() == 'hide' and health()
                report['checks'].append('packaged_first_close_hide_keeps_backend_alive_and_saves_choice')
                duplicate = subprocess.run([str(project / 'dist/AgentWorkbench/AgentWorkbench.exe'),
                                            '--data-dir', str(root)], cwd=root, env=environment,
                                           stdout=log, stderr=log, timeout=30)
                assert duplicate.returncode == 0
                wait(lambda: user32.IsWindowVisible(main_window))
                assert health()
                assert len([h for h, title in windows() if title == 'AgentWorkbench']) == 1
                report['checks'].append('relaunch_restores_tray_window_without_second_backend')
                user32.ShowWindow(main_window, 6)
                duplicate = subprocess.run([str(project / 'dist/AgentWorkbench/AgentWorkbench.exe'),
                                            '--data-dir', str(root)], cwd=root, env=environment,
                                           stdout=log, stderr=log, timeout=30)
                user32.IsIconic.argtypes = [wintypes.HWND]
                assert duplicate.returncode == 0
                wait(lambda: not user32.IsIconic(main_window))
                report['checks'].append('relaunch_restores_minimized_window')
                user32.PostMessageW(main_window, 0x0010, 0, 0)
                wait(lambda: not user32.IsWindowVisible(main_window))
                assert not any(title == '关闭 AgentWorkbench' and user32.IsWindowVisible(h) for h, title in windows())
                report['checks'].append('packaged_remembered_hide_skips_prompt')
                # Only change the test directory preference, never user application data.
                preference.save('exit')
                user32.PostMessageW(main_window, 0x0010, 0, 0)
                child.wait(timeout=20)
                assert child.returncode == 0 and not health()
                report['checks'].append('packaged_exit_preference_stops_process_and_backend')
                report['status'] = 'passed'
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
            (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
