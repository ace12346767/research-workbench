from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlsplit

import uvicorn

from agent_workbench.bootstrap import build_runtime, load_env_file
from agent_workbench.server.api import create_app


def desktop_icon_path() -> Path:
    from agent_workbench.bootstrap import resource_path
    return resource_path('assets/AgentWorkbench.ico')


def _set_app_id(value):
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(value)


def configure_desktop_identity():
    if sys.platform == 'win32':
        _set_app_id('AgentWorkbench.Desktop')


def apply_window_icon(window):
    if sys.platform != 'win32':
        return
    from System import Action
    from System.Drawing import Icon
    def apply():
        window.native.Icon = Icon(str(desktop_icon_path()))
        from agent_workbench.desktop_theme import apply_light_caption
        apply_light_caption(window.native)
    window.native.Invoke(Action(apply))


def ensure_standard_streams() -> list[Any]:
    opened = []
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            stream = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, stream)
            opened.append(stream)
    return opened


def write_crash_log() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AgentWorkbench" / "logs"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "crash.log"
    target.write_text(traceback.format_exc(), encoding="utf-8")
    return target


def write_console(message: str, *, stream: Any | None) -> None:
    if stream is None:
        return
    stream.write(message + "\n")
    stream.flush()


def find_available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(port: int, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/health/live"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok":
                return payload
        except (OSError, URLError, ValueError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise TimeoutError(f"AgentWorkbench backend did not become ready: {last_error}")


class DesktopBridge:
    def __init__(self, window: Any | None = None) -> None:
        # pywebview recursively exports public attributes of js_api objects.
        self._window = window

    def choose_workspace(self) -> str | None:
        if self._window is None:
            return None
        import webview

        selected = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not selected:
            return None
        return str(selected[0] if isinstance(selected, (list, tuple)) else selected)

    def copy_text(self, text: str) -> bool:
        if not isinstance(text, str) or len(text) > 2000000:
            raise ValueError('Clipboard text is invalid or too large')
        if self._window is None or sys.platform != 'win32':
            return False
        from System import Action
        from System.Windows.Forms import Clipboard
        def copy():
            if text:
                Clipboard.SetText(text)
            else:
                Clipboard.Clear()
        self._window.native.Invoke(Action(copy))
        return True

    def open_external_url(self, url: str) -> bool:
        import webbrowser

        if (not isinstance(url, str) or len(url) > 8192 or '\\' in url
                or any(ord(character) <= 32 or ord(character) == 127 for character in url)):
            raise ValueError('Only explicit HTTP(S) web links are allowed')
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError('Only explicit HTTP(S) web links are allowed')
        _ = parsed.port  # Validate malformed or out-of-range ports before opening.
        return webbrowser.open(url, new=2)


def _server(runtime: Any, port: int) -> uvicorn.Server:
    config = uvicorn.Config(
        create_app(runtime),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        # Loopback HTTP needs no asyncio subprocesses. Avoid Windows IOCP accept failures
        # when startup health-check clients disconnect before initialization finishes.
        loop='asyncio:SelectorEventLoop' if sys.platform == 'win32' else 'auto',
    )
    return uvicorn.Server(config)


def _start_server(server: uvicorn.Server) -> threading.Thread:
    thread = threading.Thread(target=server.run, name="awb-api", daemon=True)
    thread.start()
    return thread


def run_smoke(runtime: Any, port: int) -> dict[str, Any]:
    server = _server(runtime, port)
    thread = _start_server(server)
    try:
        return wait_for_health(port)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def run_desktop(runtime: Any, port: int, instance=None) -> None:
    import webview

    if hasattr(runtime, 'data_dir'):
        log_dir = Path(runtime.data_dir) / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=log_dir / 'desktop.log', encoding='utf-8', level=logging.WARNING)

    configure_desktop_identity()

    server = _server(runtime, port)
    thread = _start_server(server)
    try:
        wait_for_health(port)
        bridge = DesktopBridge()
        window = webview.create_window(
            "AgentWorkbench",
            f"http://127.0.0.1:{port}/",
            js_api=bridge,
            width=1360,
            height=880,
            min_size=(960, 600),
            background_color="#eef1f4",
        )
        bridge._window = window
        window.events.shown += lambda: apply_window_icon(window)
        if sys.platform == 'win32':
            from agent_workbench.desktop_tray import install_tray
            pending_close = threading.Event()
            def install_desktop_tray():
                install_tray(window, runtime.data_dir, desktop_icon_path())
                logging.getLogger(__name__).warning('Desktop tray installed before display')
            def allow_close():
                if not window.events.loaded.is_set():
                    pending_close.set()
                    return False
                return True
            def loaded():
                if instance is not None:
                    from agent_workbench.desktop_instance import activate_window
                    instance.listen(lambda: activate_window(window))
                if pending_close.is_set():
                    pending_close.clear()
                    window.destroy()
            window.events.before_show += install_desktop_tray
            window.events.closing += allow_close
            window.events.loaded += loaded
        webview.start(gui="edgechromium")
    finally:
        for request_id in list(runtime.active_cancellations):
            runtime.cancel(request_id)
        server.should_exit = True
        thread.join(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentWorkbench desktop agent")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--server-only", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()

    load_env_file(Path(__file__).resolve().parents[1] / ".env")
    instance = None
    if sys.platform == 'win32' and not args.smoke and not args.server_only:
        from agent_workbench.bootstrap import default_data_dir
        from agent_workbench.data_location import resolve_data_dir
        from agent_workbench.desktop_instance import DesktopInstance
        default = default_data_dir()
        selected = args.data_dir if args.data_dir is not None else resolve_data_dir(default, default / 'data-location.json')
        instance = DesktopInstance(selected)
    try:
        if instance is not None and not instance.acquire():
            return 0
        return run_selected_mode(args, instance)
    finally:
        if instance is not None:
            instance.close()


def run_selected_mode(args, instance=None):
    runtime = build_runtime(data_dir=args.data_dir)
    port = args.port or find_available_port()

    if args.smoke:
        write_console(json.dumps(run_smoke(runtime, port), ensure_ascii=False), stream=sys.stdout)
        return 0
    if args.server_only:
        write_console(f"AgentWorkbench: http://127.0.0.1:{port}/", stream=sys.stdout)
        _server(runtime, port).run()
        return 0
    run_desktop(runtime, port, instance)
    return 0


if __name__ == "__main__":
    ensure_standard_streams()
    try:
        exit_code = main()
    except Exception:
        write_crash_log()
        raise
    raise SystemExit(exit_code)
