import json
import os
import sys
import tempfile

import pytest

# davra_lib reads these at import time, so point it at a scratch installation before importing
_installDir = tempfile.mkdtemp(prefix="davra-test-install-")
_logDir = tempfile.mkdtemp(prefix="davra-test-log-")
os.environ["DAVRA_INSTALL_DIR"] = _installDir
os.environ["DAVRA_LOG_DIR"] = _logDir
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "davra-agent"))

import davra_lib  # noqa: E402

TEST_TOKEN = "test-api-token-0123456789"
BASE_CONFIG = {
    "server": "https://tenant.davra.com",
    "UUID": "device-uuid-1",
    "apiToken": TEST_TOKEN,
    "heartbeatInterval": 600,
    "scriptMaxTime": 30,
    "capabilities": {},
    "localMqttAgentPassword": "local-agent-password-123",
}


class FakeResponse(object):
    status_code = 200
    content = b"{}"


@pytest.fixture(autouse=True)
def config(monkeypatch):
    """Fresh config on disk for every test; never talk to a real server."""
    for name in os.listdir(_installDir):
        path = os.path.join(_installDir, name)
        if os.path.isdir(path):
            import shutil
            shutil.rmtree(path)
        else:
            os.remove(path)
    with open(davra_lib.agentConfigFile, "w") as f:
        json.dump(BASE_CONFIG, f)
    davra_lib.loadConfiguration()
    sent = []
    monkeypatch.setattr(davra_lib.requests, "request",
                        lambda method, url, **kw: sent.append((method, url, kw)) or FakeResponse())
    davra_lib.sentRequests = sent
    yield davra_lib.conf


@pytest.fixture
def installDir():
    return _installDir


@pytest.fixture
def logFile():
    return davra_lib.logFile
