from __future__ import annotations

import json
import sys
import threading
from types import SimpleNamespace

import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_workbench.app import ensure_standard_streams, find_available_port, wait_for_health, write_console


def test_clipboard_bridge_uses_native_ui_thread_without_touching_system_clipboard(monkeypatch):
    from agent_workbench.app import DesktopBridge
    calls=[]
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'System', SimpleNamespace(Action=lambda action: action))
    monkeypatch.setitem(sys.modules, 'System.Windows.Forms', SimpleNamespace(
        Clipboard=SimpleNamespace(SetText=lambda text:calls.append(text), Clear=lambda:calls.append(''))))
    def invoke(action):
        calls.append('invoke')
        action()
    bridge=DesktopBridge(SimpleNamespace(native=SimpleNamespace(Invoke=invoke)))
    assert bridge.copy_text('## test\n中文') is True
    assert calls==['invoke','## test\n中文']
    assert DesktopBridge().copy_text('text') is False
    with pytest.raises(ValueError):
        bridge.copy_text('x' * 2000001)


def test_windows_loopback_server_uses_selector_to_avoid_iocp_accept_failure(tmp_path, monkeypatch):
    import asyncio
    from agent_workbench.app import _server
    from agent_workbench.tests.test_approval_flow import make_runtime
    monkeypatch.setattr(sys, 'platform', 'win32')
    server = _server(make_runtime(tmp_path), find_available_port())
    loop = server.config.get_loop_factory()()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_windows_icon_setup_uses_mobius_resource_and_explicit_application_id(monkeypatch):
    from agent_workbench import app
    calls=[]
    monkeypatch.setattr(app,'_set_app_id',lambda value:calls.append(value))
    app.configure_desktop_identity()
    assert calls==['AgentWorkbench.Desktop']
    assert app.desktop_icon_path().is_file()
    assert app.desktop_icon_path().name=='AgentWorkbench.ico'


def test_wait_for_health_reads_loopback_json() -> None:
    port = find_available_port()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health/live":
                self.send_response(404)
                self.end_headers()
                return
            payload = json.dumps({"status": "ok", "product": "AgentWorkbench"}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = wait_for_health(port, timeout_seconds=2)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result == {"status": "ok", "product": "AgentWorkbench"}


def test_write_console_is_safe_without_stdout() -> None:
    write_console("smoke ok", stream=None)


def test_windowed_entrypoint_supplies_missing_standard_streams(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    opened = ensure_standard_streams()
    try:
        assert sys.stdout is not None
        assert sys.stderr is not None
    finally:
        for stream in opened:
            stream.close()


def test_desktop_health_failure_stops_started_backend(monkeypatch):
    from agent_workbench import app

    server = SimpleNamespace(should_exit=False)
    stopped = threading.Event()

    def run_server():
        while not server.should_exit:
            stopped.wait(0.01)
        stopped.set()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    monkeypatch.setitem(sys.modules, 'webview', SimpleNamespace())
    monkeypatch.setattr(app, '_server', lambda *_: server)
    monkeypatch.setattr(app, '_start_server', lambda *_: thread)

    def fail_health(*_):
        raise TimeoutError('test startup timeout')

    monkeypatch.setattr(app, 'wait_for_health', fail_health)
    try:
        with pytest.raises(TimeoutError):
            app.run_desktop(SimpleNamespace(active_cancellations={}), 12345)
        assert stopped.wait(0.5), 'backend kept running after desktop startup failed'
        assert not thread.is_alive()
    finally:
        server.should_exit = True
        thread.join(timeout=1)


def test_desktop_bridge_exports_only_scoped_user_actions(monkeypatch):
    from agent_workbench.app import DesktopBridge
    from webview import util

    class NativeWindow:
        def create_file_dialog(self, *_):
            return ['D:/demo-workspace']

        def destroy(self):
            raise AssertionError('native window must not be exported')

    bridge = DesktopBridge(NativeWindow())
    assert bridge.choose_workspace() == 'D:/demo-workspace'
    scripts = []
    events = SimpleNamespace(before_load=threading.Event(), loaded=threading.Event(),
                             _pywebviewready=threading.Event())
    window = SimpleNamespace(_js_api=bridge, _functions={}, _expose_lock=threading.Lock(),
                             run_js=scripts.append, events=events)
    monkeypatch.setattr(util, 'load_js_files', lambda *_: ('bootstrap', '%(functions)s'))
    util.inject_pywebview('edgechromium', window)
    assert events.loaded.wait(2)
    assert json.loads(scripts[-1]) == [
        {'func': 'choose_workspace', 'params': []},
        {'func': 'copy_text', 'params': ['text']},
        {'func': 'open_external_url', 'params': ['url']},
    ]
