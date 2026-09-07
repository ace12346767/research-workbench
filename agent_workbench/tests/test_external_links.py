import pytest

from agent_workbench.app import DesktopBridge


@pytest.mark.parametrize('url', [
    'javascript:alert(1)', 'file:///C:/Windows', 'data:text/html,test',
    '//example.com', '/api/settings', 'https://user:secret@example.com',
    'https://example.com\n/evil', 'https://', 'https://example.com:bad',
])
def test_bridge_rejects_unsafe_external_destinations(url):
    with pytest.raises(ValueError):
        DesktopBridge().open_external_url(url)


def test_bridge_opens_valid_web_link_in_system_browser(monkeypatch):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, 'open', lambda url, new: opened.append((url, new)) or True)
    assert DesktopBridge().open_external_url('https://example.com/paper?q=1') is True
    assert opened == [('https://example.com/paper?q=1', 2)]
