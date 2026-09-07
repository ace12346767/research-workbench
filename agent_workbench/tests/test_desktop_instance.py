import subprocess
import sys
import threading

import pytest

from agent_workbench.desktop_instance import DesktopInstance


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows desktop')


def child(path):
    code = ('from agent_workbench.desktop_instance import DesktopInstance; '
            'from pathlib import Path; '
            f'g=DesktopInstance(Path({str(path)!r})); '
            'print(g.acquire(), flush=True); g.close()')
    return subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=15)


def test_duplicate_queues_activation_before_window_ready(tmp_path):
    guard = DesktopInstance(tmp_path)
    try:
        assert guard.acquire()
        result = child(tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'False'
        activated = threading.Event()
        guard.listen(activated.set)
        assert activated.wait(3)
        assert child(tmp_path / 'other').stdout.strip() == 'True'
    finally:
        guard.close()
    assert child(tmp_path).stdout.strip() == 'True'


def test_crashed_owner_can_restart(tmp_path):
    code = ('from agent_workbench.desktop_instance import DesktopInstance; '
            'import time; from pathlib import Path; '
            f'g=DesktopInstance(Path({str(tmp_path)!r})); '
            'print(g.acquire(), flush=True); time.sleep(30)')
    owner = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True)
    try:
        assert owner.stdout.readline().strip() == 'True'
    finally:
        owner.kill()
        owner.wait(timeout=5)
    assert child(tmp_path).stdout.strip() == 'True'


def test_duplicate_exits_before_building_runtime(tmp_path, monkeypatch):
    from agent_workbench import app
    class Duplicate:
        def __init__(self, path):
            assert path == tmp_path
        def acquire(self):
            return False
        def close(self):
            pass
    monkeypatch.setattr('agent_workbench.desktop_instance.DesktopInstance', Duplicate)
    monkeypatch.setattr(sys, 'argv', ['awb', '--data-dir', str(tmp_path)])
    monkeypatch.setattr(app, 'load_env_file', lambda *a: None)
    def forbidden(**kwargs):
        pytest.fail('duplicate must not open runtime or data')
    monkeypatch.setattr(app, 'build_runtime', forbidden)
    assert app.main() == 0
