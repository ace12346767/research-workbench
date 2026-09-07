from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def move_redirected_file(source, target):
    import ctypes
    from ctypes import wintypes
    move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
    move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move.restype = wintypes.BOOL
    # Cross-volume redirection cannot use rename; request copy/delete and flush.
    if not move(str(source), str(target), 1 | 2 | 8):
        raise ctypes.WinError(ctypes.get_last_error())


class ClosePreference:
    def __init__(self, path: Path):
        self.path = path

    def load(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))['close_action']
            return value if value in {'ask', 'hide', 'exit'} else 'ask'
        except (OSError, ValueError, KeyError, TypeError):
            return 'ask'

    def save(self, action):
        if action not in {'ask', 'hide', 'exit'}:
            raise ValueError('Invalid window close action')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.desktop-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as file:
                json.dump({'close_action': action}, file)
                file.flush()
                os.fsync(file.fileno())
            try:
                os.replace(name, self.path)
            except OSError as error:
                if getattr(error, 'winerror', None) != 17:
                    raise
                move_redirected_file(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)


class CloseController:
    def __init__(self, preference, ui):
        self.preference, self.ui = preference, ui
        self.exiting = False
        self.prompting = False

    def close(self, *, system_shutdown=False):
        if self.exiting or system_shutdown:
            return True
        if self.prompting:
            return False
        self.prompting = True
        try:
            action = self.preference.load()
            if action == 'ask':
                action, remember = self.ui.ask()
                if action not in {'hide', 'exit'}:
                    return False
                if remember:
                    self.preference.save(action)
            if action == 'hide':
                self.ui.hide()
                return False
            return True
        except Exception as error:
            self.ui.error(error)
            return False
        finally:
            self.prompting = False

    def exit(self):
        self.exiting = True
        self.ui.close_window()

    def reset(self):
        try:
            self.preference.save('ask')
            self.ui.show()
        except Exception as error:
            self.ui.error(error)


class WindowsTray:
    """All native controls and callbacks live on the WinForms UI thread."""
    def __init__(self, window, preference, icon_path):
        from System.Drawing import Icon
        from System.Windows.Forms import ContextMenuStrip, NotifyIcon, ToolStripMenuItem
        self.window = window
        self.native = window.native
        self.controller = CloseController(preference, self)
        self.icon = Icon(str(icon_path))
        self.menu = ContextMenuStrip()
        self.open_item = ToolStripMenuItem('打开 AgentWorkbench')
        self.reset_item = ToolStripMenuItem('重新选择关闭方式')
        self.exit_item = ToolStripMenuItem('退出应用')
        self.open_item.Click += lambda *_: self.show()
        self.reset_item.Click += lambda *_: self.controller.reset()
        self.exit_item.Click += lambda *_: self.controller.exit()
        for item in (self.open_item, self.reset_item, self.exit_item):
            self.menu.Items.Add(item)
        self.tray = NotifyIcon()
        self.tray.Icon = self.icon
        self.tray.Text = 'AgentWorkbench'
        self.tray.ContextMenuStrip = self.menu
        self.tray.DoubleClick += lambda *_: self.show()
        self.tray.Visible = True
        self.native.FormClosing += self.on_closing
        self.native.FormClosed += lambda *_: self.dispose()
        self.native.VisibleChanged += self.publish_visibility
        self.native.EnabledChanged += self.publish_visibility
        self.native.Resize += self.publish_visibility
        self.window.events.loaded += self.on_loaded
        self.dialog = None

    def on_loaded(self):
        from System import Action
        if not self.native.IsDisposed:
            self.native.BeginInvoke(Action(self.publish_visibility))

    def publish_visibility(self, *_):
        from System.Windows.Forms import FormWindowState
        view = self.native.browser.webview
        if view.CoreWebView2 is None:
            return
        visible = self.native.Visible and self.native.Enabled and self.native.WindowState != FormWindowState.Minimized
        literal = 'true' if visible else 'false'
        # Fire-and-forget on the UI thread; evaluate_js would block this native callback.
        view.ExecuteScriptAsync("window.__awbNativeVisible=" + literal + ";window.dispatchEvent(new Event('awb-visibility'));")

    def on_closing(self, sender, args):
        from System.Windows.Forms import CloseReason
        if args.Cancel:
            return
        shutdown = args.CloseReason in (CloseReason.WindowsShutDown, CloseReason.TaskManagerClosing)
        args.Cancel = not self.controller.close(system_shutdown=shutdown)

    def ask(self):
        from System.Drawing import Color, Font, FontStyle, Point, Size
        from System.Windows.Forms import (AutoScaleMode, Button, CheckBox, DialogResult,
                                         Form, FormBorderStyle, FormStartPosition, Label)
        def control(kind, **properties):
            instance = kind()
            for name, value in properties.items():
                setattr(instance, name, value)
            return instance
        dialog = Form()
        self.dialog = dialog
        dialog.Text = '关闭 AgentWorkbench'
        dialog.Icon = self.icon
        dialog.ClientSize = Size(500, 224)
        dialog.AutoScaleMode = AutoScaleMode.Dpi
        dialog.FormBorderStyle = FormBorderStyle.FixedDialog
        dialog.StartPosition = FormStartPosition.CenterParent
        dialog.MaximizeBox = dialog.MinimizeBox = False
        dialog.ShowInTaskbar = False
        dialog.BackColor = Color.White
        dialog.Font = Font('Microsoft YaHei UI', 9)
        title = control(Label, Text='退出应用，还是隐藏到托盘？', Location=Point(24, 22), Size=Size(450, 30))
        title.Font = Font(dialog.Font, FontStyle.Bold)
        detail = control(Label, Text='隐藏后当前任务会继续运行，可从托盘重新打开。\n退出应用会停止后台服务。', Location=Point(24, 62), Size=Size(450, 48))
        remember = control(CheckBox, Text='记住我的选择', Checked=True, Location=Point(24, 123), Size=Size(220, 28))
        cancel = control(Button, Text='取消', Location=Point(126, 175), Size=Size(108, 32), DialogResult=DialogResult.Cancel)
        hide = control(Button, Text='隐藏到托盘', Location=Point(244, 175), Size=Size(112, 32), DialogResult=DialogResult.Yes)
        exit_button = control(Button, Text='退出应用', Location=Point(366, 175), Size=Size(110, 32), DialogResult=DialogResult.No)
        for control in (title, detail, remember, cancel, hide, exit_button):
            dialog.Controls.Add(control)
        dialog.CancelButton = cancel
        dialog.AcceptButton = hide
        from agent_workbench.desktop_theme import apply_light_caption
        dialog.Shown += lambda *_: apply_light_caption(dialog)
        try:
            result = dialog.ShowDialog(self.native)
            action = 'hide' if result == DialogResult.Yes else 'exit' if result == DialogResult.No else None
            return action, bool(remember.Checked)
        finally:
            dialog.Dispose()
            self.dialog = None

    def hide(self):
        self.tray.Visible = True
        self.native.Hide()

    def show(self):
        from System.Windows.Forms import FormWindowState
        self.native.Show()
        if self.native.WindowState == FormWindowState.Minimized:
            self.native.WindowState = FormWindowState.Normal
        self.native.Activate()

    def close_window(self):
        self.native.Close()

    def error(self, error):
        from agent_workbench.desktop_theme import show_desktop_error
        show_desktop_error(self.native, self.icon, error)

    def dispose(self):
        self.tray.Visible = False
        self.tray.Dispose()
        self.menu.Dispose()
        self.icon.Dispose()


def install_tray(window, data_dir, icon_path):
    from System import Action
    def install():
        if not hasattr(window, '_desktop_tray'):
            window._desktop_tray = WindowsTray(window, ClosePreference(Path(data_dir) / 'desktop-preferences.json'), icon_path)
    if window.native.InvokeRequired:
        window.native.Invoke(Action(install))
    else:
        install()
