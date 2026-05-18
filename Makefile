PYTHON ?= python3
PREFIX ?= $(HOME)/.local
CONFIG_HOME ?= $(HOME)/.config
TERMINATOR_PLUGIN_DIR ?= $(CONFIG_HOME)/terminator/plugins
BIN_DIR ?= $(PREFIX)/bin
TERMINATOR_SRC ?=

PYTHONPATH_VALUE := $(CURDIR)$(if $(TERMINATOR_SRC),:$(TERMINATOR_SRC),)

.PHONY: help install uninstall test check clean

help:
	@printf '%s\n' \
		'Targets:' \
		'  install    Install plugin and terminator-attach for the current user' \
		'  uninstall  Remove installed plugin and CLI' \
		'  test       Run pytest; set TERMINATOR_SRC=/path/to/terminator if needed' \
		'  check      Compile Python files and run tests' \
		'  clean      Remove generated Python and pytest caches'

install:
	install -d "$(TERMINATOR_PLUGIN_DIR)" "$(BIN_DIR)"
	install -m 0644 terminatorlib/plugins/terminal_attach.py "$(TERMINATOR_PLUGIN_DIR)/terminal_attach.py"
	install -m 0755 terminator-attach "$(BIN_DIR)/terminator-attach"

uninstall:
	rm -f "$(TERMINATOR_PLUGIN_DIR)/terminal_attach.py"
	rm -f "$(BIN_DIR)/terminator-attach"

test:
	PYTHONPATH="$(PYTHONPATH_VALUE)" $(PYTHON) -m pytest tests

check:
	$(PYTHON) -m py_compile terminator-attach terminatorlib/plugins/terminal_attach.py tests/test_terminal_attach.py
	$(MAKE) test

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache
