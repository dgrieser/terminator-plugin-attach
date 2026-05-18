# Terminator Attach Plugin

Remote-control one already-running Terminator terminal from another shell, such as an SSH login to the same machine.

This is a Terminator plugin plus a small CLI named `terminator-attach`. It exposes only terminals you explicitly share, unless configured otherwise, and uses a same-user Unix socket instead of a TCP listener.

## Status

Prototype implementation.

The current transport mirrors VTE screen text and forwards remote input into the existing terminal child process. That means shell state is preserved: current directory, environment, shell variables, aliases, history, foreground programs, and other session state remain the real Terminator session state.

It is not a full PTY multiplexer. Color fidelity, mouse reporting, sixel graphics, hyperlinks, and raw escape stream replay are not guaranteed.

## Requirements

- Terminator with Python plugin support.
- Python 3 for the `terminator-attach` CLI and tests.
- Same Unix user for Terminator and the SSH/client shell.
- `pytest` only for running tests.

## Install

From this repository:

```sh
make install
```

This installs:

- `terminal_attach.py` to `~/.config/terminator/plugins/`
- `terminator-attach` to `~/.local/bin/`

Ensure `~/.local/bin` is in `PATH`.

Then enable the plugin in Terminator:

1. Open Terminator preferences.
2. Go to Plugins.
3. Enable `TerminalAttach`.
4. Restart Terminator if the plugin was not loaded yet.

## Usage

In Terminator, right-click a terminal and choose `Share terminal attach`.

From another shell as the same user:

```sh
terminator-attach list
terminator-attach connect <terminal-uuid>
```

While attached, the local Terminator terminal is made read-only. It is restored when the remote disconnects.

Disconnect options:

```sh
terminator-attach disconnect <terminal-uuid>
```

or use the Terminator context menu action `Disconnect terminal attach`.

Stop sharing:

```sh
terminator-attach unshare <terminal-uuid>
```

or use `Unshare terminal attach` from the context menu.

## Configuration

Default config is explicit sharing only.

To share all terminals by default, add plugin config to `~/.config/terminator/config`:

```ini
[plugins]
  [[TerminalAttach]]
    share_by_default = True
```

Even with `share_by_default = True`, an individual terminal can still be unshared for the current Terminator process.

## Security Model

The plugin creates a Unix socket at:

```text
$XDG_RUNTIME_DIR/terminator-attach.sock
```

If `XDG_RUNTIME_DIR` is not set, it falls back to `/run/user/<uid>` and then `/tmp/terminator-<uid>`.

The socket is created with mode `0600`, so it is intended for the same Unix user only. There is no network listener and no cross-user authentication layer.

## Behavior and Limits

- Only shared terminals appear in `terminator-attach list`.
- One remote connection per Terminator window is allowed.
- The remote cannot resize the Terminator terminal.
- Input is forwarded to the existing shell/process through VTE.
- The remote display is a rendered text snapshot, not a byte-exact terminal stream.

## Development

Run tests against an installed Terminator:

```sh
make test
```

Run tests against a local Terminator checkout:

```sh
make test TERMINATOR_SRC=/path/to/gnome-terminator/terminator
```

Run compile checks plus tests:

```sh
make check TERMINATOR_SRC=/path/to/gnome-terminator/terminator
```

Clean generated files:

```sh
make clean
```

## Uninstall

```sh
make uninstall
```
