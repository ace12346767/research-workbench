"""Light native chrome, retaining Windows caption controls and accessibility."""
import ctypes
import sys


def apply_light_caption(form):
    if sys.platform != 'win32':
        return
    try:
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        # COLORREF is 0x00BBGGRR. Unsupported attributes are harmless on older Windows.
        results = {}
        for attribute, value in ((20, 0), (35, 0x00FFFFFF), (36, 0x00372922), (34, 0x00ECE6DF)):
            color = ctypes.c_uint(value)
            results[attribute] = set_attribute(int(form.Handle.ToInt64()), attribute, ctypes.byref(color), ctypes.sizeof(color))
        return results
    except (AttributeError, OSError):
        pass


def show_desktop_error(owner, icon, error):
    from System.Drawing import Color, Font, Point, Size
    from System.Windows.Forms import Button, DialogResult, Form, FormBorderStyle, FormStartPosition, Label, TextBox
    dialog = Form()
    dialog.Text = 'AgentWorkbench'
    dialog.Icon = icon
    dialog.ClientSize = Size(490, 195)
    dialog.BackColor = Color.White
    dialog.Font = Font('Microsoft YaHei UI', 10)
    dialog.FormBorderStyle = FormBorderStyle.FixedDialog
    dialog.StartPosition = FormStartPosition.CenterParent
    dialog.MaximizeBox = dialog.MinimizeBox = dialog.ShowInTaskbar = False
    label = Label(); label.Text = '无法保存关闭设置，窗口已保留。\n请重试；详细信息可用于排查问题。'
    label.Location = Point(24, 25); label.Size = Size(442, 66)
    details = TextBox(); details.Multiline = True; details.ReadOnly = True
    details.Text = str(error); details.Visible = False
    details.Location = Point(24, 165); details.Size = Size(442, 150)
    expand = Button(); expand.Text = '详细信息'; expand.Location = Point(24, 115); expand.Size = Size(112, 34)
    okay = Button(); okay.Text = '确定'; okay.Location = Point(354, 115); okay.Size = Size(112, 34)
    okay.DialogResult = DialogResult.OK
    def toggle(*_):
        details.Visible = not details.Visible
        dialog.ClientSize = Size(490, 340 if details.Visible else 195)
        expand.Text = '收起详情' if details.Visible else '详细信息'
    expand.Click += toggle
    for control in (label, expand, okay, details): dialog.Controls.Add(control)
    dialog.AcceptButton = dialog.CancelButton = okay
    dialog.Shown += lambda *_: apply_light_caption(dialog)
    try: dialog.ShowDialog(owner)
    finally: dialog.Dispose()
