"""Test configuration.

The tests talk to a simulated camera on 127.0.0.1, so real sockets are needed.
pytest-homeassistant-custom-component blocks them before every test; turn that
blocking off for this test suite.
"""

from pathlib import Path
import sys

# Make Home Assistant find this repository's custom_components package.
sys.path.insert(0, str(Path(__file__).parents[1]))
import custom_components  # noqa: E402,F401

try:
    import pytest_socket
except ImportError:  # only the protocol tests are run
    pytest_socket = None

if pytest_socket is not None:
    pytest_socket.disable_socket = lambda *args, **kwargs: None
    pytest_socket.socket_allow_hosts = lambda *args, **kwargs: None
