#!/usr/bin/env python
# Terminator by Chris Jones <cmsj@tenshu.net>
# GPL v2 only

import io
import json
import uuid

import pytest

from terminatorlib.plugins.terminal_attach import (
    AttachError,
    AttachManager,
    AttachSession,
)


class DirectMain(object):
    def call(self, func, *args, **kwargs):
        return func(*args, **kwargs)


class FakeUUID(object):
    def __init__(self, value):
        self.urn = value


class FakeWindow(object):
    def __init__(self, title):
        self.title = title

    def get_title(self):
        return self.title


class FakeVTE(object):
    def __init__(self):
        self.input_enabled = True
        self.columns = 80
        self.rows = 24
        self.handlers = {}
        self.next_handler = 1

    def get_input_enabled(self):
        return self.input_enabled

    def set_input_enabled(self, value):
        self.input_enabled = value

    def connect(self, name, _handler):
        handler_id = self.next_handler
        self.next_handler += 1
        self.handlers[handler_id] = name
        return handler_id

    def disconnect(self, handler_id):
        del self.handlers[handler_id]

    def get_column_count(self):
        return self.columns

    def get_row_count(self):
        return self.rows

    def get_cursor_position(self):
        return 3, 5

    def get_text_range(self, *_args):
        return 'prompt$ echo ok\nok\n', None

    def get_text_range_format(self, *_args):
        return 'prompt$ echo ok\nok\n', None


class FakeTerminal(object):
    def __init__(self, name, window):
        self.uuid = FakeUUID('urn:uuid:' + str(uuid.uuid4()))
        self.name = name
        self.window = window
        self.vte = FakeVTE()
        self.fed = []

    def get_vte(self):
        return self.vte

    def get_toplevel(self):
        return self.window

    def get_window_title(self):
        return self.name

    def get_cwd(self):
        return '/tmp'

    def feed(self, payload):
        self.fed.append(payload)


class FakeTerminator(object):
    def __init__(self, terminals):
        self.terminals = terminals

    def find_terminal_by_uuid(self, term_uuid):
        for terminal in self.terminals:
            if terminal.uuid.urn == term_uuid:
                return terminal
        return None


class FakeRequest(object):
    def __init__(self):
        self.wfile = io.BytesIO()

    def events(self):
        self.wfile.seek(0)
        return [
            json.loads(line.decode('utf-8'))
            for line in self.wfile.readlines()
        ]


def make_manager(config=None):
    window = FakeWindow('window')
    terminal = FakeTerminal('terminal', window)
    other = FakeTerminal('other', window)
    manager = AttachManager(
        terminator=FakeTerminator([terminal, other]),
        config=config or {'share_by_default': False},
        main=DirectMain(),
    )
    return manager, terminal, other


def test_list_only_explicitly_shared_terminal():
    manager, terminal, _other = make_manager()

    assert manager.list_sessions()['sessions'] == []

    manager.share(terminal.uuid.urn)
    sessions = manager.list_sessions()['sessions']

    assert [item['uuid'] for item in sessions] == [terminal.uuid.urn]
    assert sessions[0]['columns'] == 80
    assert sessions[0]['rows'] == 24


def test_share_by_default_can_be_overridden_by_unshare():
    manager, terminal, other = make_manager({'share_by_default': True})

    assert len(manager.list_sessions()['sessions']) == 2

    manager.unshare(terminal.uuid.urn)
    sessions = manager.list_sessions()['sessions']

    assert [item['uuid'] for item in sessions] == [other.uuid.urn]


def test_one_attached_terminal_per_window():
    manager, terminal, other = make_manager()
    manager.share(terminal.uuid.urn)
    manager.share(other.uuid.urn)

    request = FakeRequest()
    session = manager.connect(terminal.uuid.urn, request)

    with pytest.raises(AttachError):
        manager.connect(other.uuid.urn, FakeRequest())

    session.close('test')


def test_session_sets_readonly_and_restores_input_state():
    manager, terminal, _other = make_manager()
    manager.share(terminal.uuid.urn)
    request = FakeRequest()
    session = manager.connect(terminal.uuid.urn, request)

    session.start()

    assert terminal.vte.input_enabled is False
    assert terminal.uuid.urn in manager.sessions

    session.close('test')

    assert terminal.vte.input_enabled is True
    assert terminal.uuid.urn not in manager.sessions
    assert terminal.vte.handlers == {}


def test_disconnect_closes_active_session():
    manager, terminal, _other = make_manager()
    manager.share(terminal.uuid.urn)
    request = FakeRequest()
    session = manager.connect(terminal.uuid.urn, request)
    session.start()

    manager.disconnect(terminal.uuid.urn)

    assert session.closed.is_set()
    assert terminal.vte.input_enabled is True


def test_feed_forwards_to_terminal():
    manager, terminal, _other = make_manager()
    session = AttachSession(manager, terminal, FakeRequest())

    session.feed(b'echo hi\n')

    assert terminal.fed == [b'echo hi\n']
