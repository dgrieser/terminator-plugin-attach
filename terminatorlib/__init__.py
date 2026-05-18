"""Namespace package shim for standalone Terminator plugins."""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
