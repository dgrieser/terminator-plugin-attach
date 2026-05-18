"""terminal_attach.py - remotely control one shared Terminator terminal."""

import base64
import json
import os
import stat
import threading
import time
try:
    import socketserver
except ImportError:
    import SocketServer as socketserver

try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Vte', '2.91')
    from gi.repository import GLib, Gtk, Vte
except (ImportError, ValueError):
    GLib = None
    Gtk = None
    Vte = None

import terminatorlib.plugin as plugin
from terminatorlib.config import Config
from terminatorlib.terminator import Terminator
from terminatorlib.translation import _
from terminatorlib.util import dbg, err

AVAILABLE = ['TerminalAttach']

SOCKET_NAME = 'terminator-attach.sock'
SNAPSHOT_INTERVAL = 0.05


def get_socket_path():
    """Return the same-user control socket path."""
    runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime_dir:
        candidate = '/run/user/%s' % os.getuid()
        if os.path.isdir(candidate):
            runtime_dir = candidate
    if not runtime_dir:
        runtime_dir = os.path.join('/tmp', 'terminator-%s' % os.getuid())
    return os.path.join(runtime_dir, SOCKET_NAME)


def _vte_text(vte, start_row, end_row, columns):
    """Return plain text from a VTE range across supported VTE versions."""
    if Vte and Vte.get_minor_version() >= 72:
        return vte.get_text_range_format(
            Vte.Format.TEXT, start_row, 0, end_row, columns
        )[0]
    return vte.get_text_range(start_row, 0, end_row, columns, None)[0]


class MainThreadCaller(object):
    """Synchronously marshal callable execution to GTK's main thread."""

    def __init__(self):
        self.thread_id = threading.get_ident()

    def call(self, func, *args, **kwargs):
        if threading.get_ident() == self.thread_id:
            return func(*args, **kwargs)

        done = threading.Event()
        result = {'value': None, 'error': None}

        def invoke():
            try:
                result['value'] = func(*args, **kwargs)
            except Exception as ex:
                result['error'] = ex
            finally:
                done.set()
            return False

        GLib.idle_add(invoke)
        done.wait()
        if result['error']:
            raise result['error']
        return result['value']


class AttachSession(object):
    """One active remote-control session."""

    def __init__(self, manager, terminal, request):
        self.manager = manager
        self.terminal = terminal
        self.request = request
        self.closed = threading.Event()
        self.last_snapshot = None
        self.snapshot_handler = None
        self.cursor_handler = None
        self.size_handler = None
        self.local_input_enabled = True
        self.last_sent = 0

    def start(self):
        self.manager.main.call(self._start_on_main)
        self._send({'event': 'attached'})
        self.send_snapshot(force=True)

    def _start_on_main(self):
        vte = self.terminal.get_vte()
        self.local_input_enabled = vte.get_input_enabled()
        vte.set_input_enabled(False)
        self.snapshot_handler = vte.connect('contents-changed', self.on_changed)
        self.cursor_handler = vte.connect('cursor-moved', self.on_changed)
        self.size_handler = vte.connect('char-size-changed', self.on_changed)

    def on_changed(self, *_args):
        now = time.time()
        if now - self.last_sent < SNAPSHOT_INTERVAL:
            return True
        self.last_sent = now
        self.send_snapshot()
        return True

    def send_snapshot(self, force=False):
        if self.closed.is_set():
            return
        try:
            snapshot = self.manager.main.call(self._snapshot_on_main)
            if force or snapshot != self.last_snapshot:
                self.last_snapshot = snapshot
                event = {'event': 'snapshot'}
                event.update(snapshot)
                self._send(event)
        except Exception as ex:
            err('TerminalAttach snapshot failed: %s' % ex)
            self.close('snapshot-error')

    def _snapshot_on_main(self):
        vte = self.terminal.get_vte()
        columns = vte.get_column_count()
        rows = vte.get_row_count()
        cursor_col, cursor_row = vte.get_cursor_position()
        start_row = max(0, cursor_row - rows + 1)
        text = _vte_text(vte, start_row, cursor_row + 1, columns)
        return {
            'columns': columns,
            'rows': rows,
            'cursor_col': cursor_col,
            'cursor_row': max(0, cursor_row - start_row),
            'text': text,
        }

    def feed(self, payload):
        if self.closed.is_set():
            return
        self.manager.main.call(self.terminal.feed, payload)

    def close(self, reason='disconnect'):
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self._send({'event': 'closed', 'reason': reason})
        except Exception:
            pass
        self.manager.main.call(self._close_on_main)
        self.manager.session_closed(self)

    def _close_on_main(self):
        vte = self.terminal.get_vte()
        for handler_id in (self.snapshot_handler, self.cursor_handler,
                           self.size_handler):
            if handler_id:
                try:
                    vte.disconnect(handler_id)
                except Exception:
                    pass
        vte.set_input_enabled(self.local_input_enabled)

    def _send(self, data):
        line = (json.dumps(data, separators=(',', ':')) + '\n').encode('utf-8')
        self.request.wfile.write(line)
        self.request.wfile.flush()


class AttachManager(object):
    """Shared plugin state and command implementation."""

    def __init__(self, terminator=None, config=None, main=None):
        self.terminator = terminator or Terminator()
        self.config = config or self.get_config()
        self.main = main or MainThreadCaller()
        self.shared = set()
        self.unshared = set()
        self.sessions = {}
        self.window_sessions = {}
        self.server = None
        self.server_thread = None
        self.socket_path = get_socket_path()

    @classmethod
    def get_config(cls):
        config = {'share_by_default': 'False'}
        user_config = Config().plugin_get_config(cls.__name__)
        if user_config:
            config.update(user_config)
        config['share_by_default'] = str(config['share_by_default']).lower() == 'true'
        return config

    def start(self):
        if self.server:
            return
        directory = os.path.dirname(self.socket_path)
        if not os.path.isdir(directory):
            os.makedirs(directory, mode=0o700)
        if os.path.exists(self.socket_path):
            mode = os.stat(self.socket_path).st_mode
            if stat.S_ISSOCK(mode):
                os.unlink(self.socket_path)
        old_umask = os.umask(0o177)
        try:
            self.server = AttachUnixServer(self.socket_path, AttachRequestHandler)
        finally:
            os.umask(old_umask)
        os.chmod(self.socket_path, 0o600)
        self.server.manager = self
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            name='terminator-attach',
            daemon=True,
        )
        self.server_thread.start()
        dbg('TerminalAttach listening on %s' % self.socket_path)

    def stop(self):
        for session in list(self.sessions.values()):
            session.close('plugin-unloaded')
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except OSError:
            pass

    def terminal_uuid(self, terminal):
        return terminal.uuid.urn

    def is_shared(self, terminal):
        if terminal in self.unshared:
            return False
        return self.config['share_by_default'] or terminal in self.shared

    def share(self, uuid):
        terminal = self.find_terminal(uuid)
        self.shared.add(terminal)
        self.unshared.discard(terminal)
        return {'ok': True}

    def unshare(self, uuid):
        terminal = self.find_terminal(uuid)
        self.shared.discard(terminal)
        self.unshared.add(terminal)
        session = self.sessions.get(self.terminal_uuid(terminal))
        if session:
            session.close('unshared')
        return {'ok': True}

    def list_sessions(self):
        result = []
        for terminal in self.terminator.terminals:
            if not self.is_shared(terminal):
                continue
            result.append(self.describe_terminal(terminal))
        return {'ok': True, 'sessions': result}

    def describe_terminal(self, terminal):
        uuid = self.terminal_uuid(terminal)
        window = terminal.get_toplevel()
        vte = terminal.get_vte()
        return {
            'uuid': uuid,
            'title': terminal.get_window_title(),
            'window_title': window.get_title(),
            'cwd': terminal.get_cwd(),
            'columns': vte.get_column_count(),
            'rows': vte.get_row_count(),
            'attached': uuid in self.sessions,
        }

    def connect(self, uuid, request):
        terminal = self.find_terminal(uuid)
        if not self.is_shared(terminal):
            raise AttachError('terminal is not shared')
        uuid = self.terminal_uuid(terminal)
        if uuid in self.sessions:
            raise AttachError('terminal already has a remote')
        window = terminal.get_toplevel()
        if window in self.window_sessions:
            raise AttachError('window already has a remote-controlled terminal')
        session = AttachSession(self, terminal, request)
        self.sessions[uuid] = session
        self.window_sessions[window] = session
        return session

    def disconnect(self, uuid):
        terminal = self.find_terminal(uuid)
        session = self.sessions.get(self.terminal_uuid(terminal))
        if not session:
            raise AttachError('terminal is not attached')
        session.close('requested')
        return {'ok': True}

    def session_closed(self, session):
        uuid = self.terminal_uuid(session.terminal)
        self.sessions.pop(uuid, None)
        window = session.terminal.get_toplevel()
        if self.window_sessions.get(window) is session:
            self.window_sessions.pop(window, None)

    def find_terminal(self, uuid):
        terminal = self.terminator.find_terminal_by_uuid(uuid)
        if not terminal:
            raise AttachError('terminal not found')
        return terminal


class AttachError(Exception):
    pass


class AttachUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class AttachRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        manager = self.server.manager
        try:
            request = self._read_json()
            command = request.get('command')
            if command == 'list':
                self._write(manager.main.call(manager.list_sessions))
            elif command == 'share':
                self._write(manager.main.call(manager.share, request.get('uuid')))
            elif command == 'unshare':
                self._write(manager.main.call(manager.unshare, request.get('uuid')))
            elif command == 'disconnect':
                self._write(manager.main.call(manager.disconnect, request.get('uuid')))
            elif command == 'connect':
                session = manager.main.call(manager.connect, request.get('uuid'), self)
                self._write({'ok': True})
                self._run_session(session)
            else:
                raise AttachError('unknown command')
        except AttachError as ex:
            self._write({'ok': False, 'error': str(ex)})
        except Exception as ex:
            err('TerminalAttach request failed: %s' % ex)
            self._write({'ok': False, 'error': str(ex)})

    def _run_session(self, session):
        session.start()
        try:
            for line in self.rfile:
                data = json.loads(line.decode('utf-8'))
                if 'input' in data:
                    session.feed(base64.b64decode(data['input']))
                elif data.get('command') == 'disconnect':
                    break
        finally:
            session.close('client-disconnected')

    def _read_json(self):
        line = self.rfile.readline()
        if not line:
            raise AttachError('empty request')
        return json.loads(line.decode('utf-8'))

    def _write(self, data):
        self.wfile.write((json.dumps(data, separators=(',', ':')) + '\n').encode('utf-8'))
        self.wfile.flush()


class TerminalAttach(plugin.MenuItem):
    """Expose selected terminals for same-user remote control."""

    capabilities = ['terminal_menu']
    manager = None

    def __init__(self):
        plugin.MenuItem.__init__(self)
        if not TerminalAttach.manager:
            TerminalAttach.manager = AttachManager()
            TerminalAttach.manager.start()
        self.manager = TerminalAttach.manager

    def unload(self):
        if TerminalAttach.manager:
            TerminalAttach.manager.stop()
            TerminalAttach.manager = None

    def callback(self, menuitems, _menu, terminal):
        uuid = self.manager.terminal_uuid(terminal)
        if self.manager.is_shared(terminal):
            item = Gtk.MenuItem.new_with_mnemonic(_('Unshare terminal attach'))
            item.connect('activate', self._unshare, uuid)
        else:
            item = Gtk.MenuItem.new_with_mnemonic(_('Share terminal attach'))
            item.connect('activate', self._share, uuid)
        menuitems.append(item)

        if uuid in self.manager.sessions:
            item = Gtk.MenuItem.new_with_mnemonic(_('Disconnect terminal attach'))
            item.connect('activate', self._disconnect, uuid)
            menuitems.append(item)

    def _share(self, _widget, uuid):
        self.manager.share(uuid)

    def _unshare(self, _widget, uuid):
        self.manager.unshare(uuid)

    def _disconnect(self, _widget, uuid):
        self.manager.disconnect(uuid)
