"""Per-data-directory Windows desktop ownership and queued activation."""
import ctypes
from ctypes import wintypes
import getpass
import hashlib
import os
import threading


class DesktopInstance:
    def __init__(self, data_dir):
        identity = getpass.getuser() + '\0' + os.path.normcase(str(data_dir.resolve()))
        self.name = 'Local\\AgentWorkbench.' + hashlib.sha256(identity.encode()).hexdigest()
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in [
            ('CreateMutexW', [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('CreateEventW', [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('WaitForSingleObject', [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ('SetEvent', [wintypes.HANDLE], wintypes.BOOL),
            ('ReleaseMutex', [wintypes.HANDLE], wintypes.BOOL),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        ]:
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.mutex = self.event = None
        self.owner = False
        self.thread = None
        self.stopped = threading.Event()

    def acquire(self):
        # Create the auto-reset event before ownership, so early launches queue a wakeup.
        self.event = self.api.CreateEventW(None, False, False, self.name + '.activate')
        self.mutex = self.api.CreateMutexW(None, False, self.name)
        if not self.event or not self.mutex:
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        status = self.api.WaitForSingleObject(self.mutex, 0)
        if status in (0, 0x80):  # Acquired, or recovered an abandoned owner.
            self.owner = True
            return True
        if status != 0x102:
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.api.SetEvent(self.event):
            raise ctypes.WinError(ctypes.get_last_error())
        return False

    def listen(self, activate):
        if not self.owner or self.thread is not None:
            return
        def watch():
            while not self.stopped.is_set():
                status = self.api.WaitForSingleObject(self.event, 500)
                if status == 0 and not self.stopped.is_set():
                    activate()
                elif status not in (0, 0x102):
                    return
        self.thread = threading.Thread(target=watch, name='awb-activation', daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.thread:
            self.api.SetEvent(self.event)
            self.thread.join(timeout=2)
        if self.owner:
            self.api.ReleaseMutex(self.mutex)
            self.owner = False
        for name in ('mutex', 'event'):
            handle = getattr(self, name)
            if handle:
                self.api.CloseHandle(handle)
                setattr(self, name, None)


def activate_window(window):
    from System import Action
    from System.Windows.Forms import FormWindowState
    def show():
        form = window.native
        if form.IsDisposed:
            return
        form.Show()
        if form.WindowState == FormWindowState.Minimized:
            form.WindowState = FormWindowState.Normal
        form.Activate()
        form.BringToFront()
    if not window.native.IsDisposed:
        window.native.BeginInvoke(Action(show))
