from unittest.mock import Mock

import pytest

from agent_workbench.desktop_tray import CloseController, ClosePreference


def test_redirected_cross_volume_preference_uses_windows_fallback(tmp_path, monkeypatch):
    import agent_workbench.desktop_tray as module
    preference = ClosePreference(tmp_path / 'desktop.json')
    error = OSError('redirected volume'); error.winerror = 17
    monkeypatch.setattr(module.os, 'replace', Mock(side_effect=error))
    def fallback(source, target):
        Path(target).write_bytes(Path(source).read_bytes())
    from pathlib import Path
    move = Mock(side_effect=fallback)
    monkeypatch.setattr(module, 'move_redirected_file', move, raising=False)
    preference.save('hide')
    assert preference.load() == 'hide'
    move.assert_called_once()


def test_permission_error_does_not_use_cross_volume_fallback(tmp_path, monkeypatch):
    import agent_workbench.desktop_tray as module
    preference = ClosePreference(tmp_path / 'desktop.json')
    preference.save('exit')
    monkeypatch.setattr(module.os, 'replace', Mock(side_effect=PermissionError('denied')))
    move = Mock(); monkeypatch.setattr(module, 'move_redirected_file', move, raising=False)
    with pytest.raises(PermissionError): preference.save('hide')
    assert preference.load() == 'exit'
    move.assert_not_called()


def test_preference_missing_corrupt_and_invalid_values(tmp_path):
    preference = ClosePreference(tmp_path / 'desktop.json')
    assert preference.load() == 'ask'
    for content in ('broken', '[]', '{"close_action":"other"}'):
        preference.path.write_text(content)
        assert preference.load() == 'ask'
    with pytest.raises(ValueError):
        preference.save('other')
    preference.save('hide')
    assert ClosePreference(preference.path).load() == 'hide'


@pytest.mark.parametrize('action', ['hide', 'exit'])
def test_first_choice_remembered_and_not_asked_again(tmp_path, action):
    ui = Mock()
    ui.ask.return_value = (action, True)
    preference = ClosePreference(tmp_path / 'desktop.json')
    controller = CloseController(preference, ui)
    assert controller.close() is (action == 'exit')
    assert preference.load() == action
    controller = CloseController(preference, ui)
    assert controller.close() is (action == 'exit')
    assert ui.ask.call_count == 1
    assert ui.hide.call_count == (2 if action == 'hide' else 0)


def test_cancel_and_unremembered_choice_leave_preference_unchanged(tmp_path):
    ui = Mock()
    preference = ClosePreference(tmp_path / 'desktop.json')
    controller = CloseController(preference, ui)
    ui.ask.return_value = (None, True)
    assert controller.close() is False
    ui.hide.assert_not_called()
    assert not preference.path.exists()
    ui.ask.return_value = ('hide', False)
    assert controller.close() is False
    assert preference.load() == 'ask'


def test_exit_and_system_shutdown_bypass_remembered_hide(tmp_path):
    ui = Mock()
    preference = ClosePreference(tmp_path / 'desktop.json')
    preference.save('hide')
    controller = CloseController(preference, ui)
    assert controller.close(system_shutdown=True) is True
    controller.exit()
    assert controller.close() is True
    ui.close_window.assert_called_once()
    ui.ask.assert_not_called()
    ui.hide.assert_not_called()


def test_reset_and_save_failure_keep_window_available(tmp_path):
    ui = Mock()
    preference = Mock()
    preference.load.return_value = 'ask'
    ui.ask.return_value = ('hide', True)
    controller = CloseController(preference, ui)
    preference.save.side_effect = OSError('disk full')
    assert controller.close() is False
    ui.hide.assert_not_called()
    ui.error.assert_called_once()
    preference.save.side_effect = None
    controller.reset()
    preference.save.assert_called_with('ask')
    ui.show.assert_called_once()


def test_reentrant_close_does_not_open_second_prompt(tmp_path):
    ui = Mock()
    controller = CloseController(ClosePreference(tmp_path / 'desktop.json'), ui)
    def ask():
        assert controller.close() is False
        return None, False
    ui.ask.side_effect = ask
    assert controller.close() is False
    ui.ask.assert_called_once()
