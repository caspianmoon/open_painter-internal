"""Sign-in requires online access, but material sharing gates only authoring.

Run with Blender --background --factory-startup --online-mode --python this_file.
No network requests or credential access.
"""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.ai import session
from open_painter.authoring.schema import AuthoringError


def run():
    prefs = SimpleNamespace(director_share_context=False)
    connection = Mock()
    saved = session.state.copy()
    try:
        with patch.object(session, 'preferences', return_value=prefs), \
             patch.object(session, 'connection', connection):
            session.ensure_online()  # Authentication never sends material data.
            session.state.update(signed_in=True, auto_login=True)
            try:
                session.run(None)
            except AuthoringError as exc:
                assert 'Share Material Context' in str(exc)
            else:
                raise AssertionError('Authoring bypassed the sharing gate')
            connection.send.assert_not_called()
            session.handle(dict(event='account', connected=False))
            connection.send.assert_called_once_with(dict(action='login'))
            assert session.state['login'] and not session.state['auto_login']
            session.handle(dict(event='account', connected=False))
            assert connection.send.call_count == 1
            connection.reset_mock()
            session.state['auto_login'] = True
            session.handle(dict(event='account', connected=True))
            assert session.state['signed_in'] and not session.state['auto_login']
            connection.send.assert_not_called()
            prefs.director_share_context = True
            session.ensure_online(share_context=True)
        with patch.object(session, 'bpy', SimpleNamespace(app=SimpleNamespace(online_access=False))):
            try:
                session.ensure_online()
            except AuthoringError as exc:
                assert 'Allow Online Access' in str(exc)
            else:
                raise AssertionError('Sign-in bypassed Blender offline mode')
    finally:
        session.state.clear()
        session.state.update(saved)
    print('DIRECTOR CONNECTION: sharing gate and automatic sign-in passed')


run()
