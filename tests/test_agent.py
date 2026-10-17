import hashlib
import io
import json
import os
import stat
import tarfile

import pytest

import davra_agent
import davra_lib
from conftest import TEST_TOKEN


class FakeMqttClient(object):
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))


@pytest.fixture
def apps(monkeypatch):
    client = FakeMqttClient()
    monkeypatch.setattr(davra_agent, "clientOfDevice", client)
    return client


@pytest.fixture
def commands(monkeypatch):
    ran = []
    monkeypatch.setattr(davra_lib, "runCommandWithTimeout",
                        lambda parts, timeout, cwd=None: ran.append((parts, cwd)) or (0, "ok"))
    return ran


def startFunction(functionName, params=None):
    davra_lib.conf["capabilities"][functionName] = {}
    davra_lib.provideFreshDirectory(davra_agent.currentFunctionDir)
    info = {"functionName": functionName, "status": "running", "startTime": davra_lib.getMilliSecondsSinceEpoch(),
            "functionParameterValues": dict(params or {}, functionUuid="fn-uuid-1")}
    davra_lib.writeJsonFile(davra_agent.currentFunctionJson, info)


def functionState():
    with open(davra_agent.currentFunctionJson) as f:
        return json.load(f)


# Local mqtt bus (C1)

def test_local_bus_cannot_run_agent_functions(monkeypatch, apps):
    calls = []
    monkeypatch.setattr(davra_agent, "runFunction", lambda *a: calls.append(a))
    davra_agent.processMessageFromAppToAgent({"runFunctionOnAgent": "agent-action-runScriptBash",
                                              "functionParameterValues": {"script": "id"}})
    assert calls == []


def test_local_bus_config_has_no_secrets(apps):
    davra_agent.processMessageFromAppToAgent({"retrieveConfigFromAgent": "true"})
    (topic, msg) = apps.published[-1]
    assert TEST_TOKEN not in json.dumps(msg)
    assert msg["agentConfig"]["apiToken"] == davra_lib.redactedValue
    assert msg["agentConfig"]["localMqttAgentPassword"] == davra_lib.redactedValue


def test_apps_cannot_register_agent_capabilities(apps):
    davra_agent.processMessageFromAppToAgent({"registerCapability": "agent-action-runScriptBash",
                                              "capabilityDetails": {}})
    davra_agent.processMessageFromAppToAgent({"registerCapability": "app-readSensor", "capabilityDetails": {}})
    assert "agent-action-runScriptBash" not in davra_lib.conf["capabilities"]
    assert "app-readSensor" in davra_lib.conf["capabilities"]


def test_app_can_only_finish_the_running_app_function(apps):
    startFunction("app-readSensor")
    davra_agent.processMessageFromAppToAgent({"finishedFunctionOnApp": "app-other", "status": "completed"})
    davra_agent.processMessageFromAppToAgent({"finishedFunctionOnApp": "app-readSensor", "status": "completed",
                                              "functionUuid": "wrong-uuid"})
    davra_agent.processMessageFromAppToAgent({"finishedFunctionOnApp": "app-readSensor", "status": "hacked"})
    assert functionState()["status"] == "running"
    davra_agent.processMessageFromAppToAgent({"finishedFunctionOnApp": "app-readSensor", "status": "completed",
                                              "response": "42", "functionUuid": "fn-uuid-1"})
    assert not os.path.exists(davra_agent.currentFunctionJson)  # finished and cleaned up


def test_app_cannot_finish_agent_functions(apps):
    davra_agent.agentCapabilityFunctions["agent-action-runScriptBash"] = lambda p: None
    startFunction("agent-action-runScriptBash")
    davra_agent.processMessageFromAppToAgent({"finishedFunctionOnApp": "agent-action-runScriptBash",
                                              "status": "completed"})
    assert functionState()["status"] == "running"


# Payload parsing (M2)

def test_payload_parsing_keeps_content_unchanged():
    script = "echo '$HOME'"
    assert davra_agent.parseMqttPayload(json.dumps({"script": script}).encode())["script"] == script
    assert davra_agent.parseMqttPayload(repr({"script": script}).encode())["script"] == script
    assert davra_agent.parseMqttPayload(b"davra.announcement:check-for-jobs") is None
    assert davra_agent.parseMqttPayload(b"[1, 2]") is None


# Remote config changes (M1)

@pytest.mark.parametrize("key", ["server", "apiToken", "UUID", "mqttBrokerServerHost", "mqttBrokerServerUseTLS",
                                 "insecureDevMode", "localMqttAgentPassword"])
def test_update_config_refuses_protected_keys(key, apps):
    startFunction("agent-action-updateAgentConfig")
    before = dict(davra_lib.conf)
    davra_agent.agentFunctionUpdateAgentConfig({"key": key, "value": "https://attacker.example"})
    assert davra_lib.conf.get(key) == before.get(key)


def test_update_config_validates_numbers_and_allows_other_keys(apps):
    startFunction("agent-action-updateAgentConfig")
    davra_agent.agentFunctionUpdateAgentConfig({"key": "heartbeatInterval", "value": "1"})
    assert davra_lib.conf["heartbeatInterval"] == 600
    startFunction("agent-action-updateAgentConfig")
    davra_agent.agentFunctionUpdateAgentConfig({"key": "heartbeatInterval", "value": "120"})
    assert davra_lib.conf["heartbeatInterval"] == 120
    startFunction("agent-action-updateAgentConfig")
    davra_agent.agentFunctionUpdateAgentConfig({"key": "siteName", "value": "Dublin"})
    assert davra_lib.conf["siteName"] == "Dublin"


# Scripts (H2)

def test_script_is_private_and_run_without_shell(commands, apps):
    startFunction("agent-action-runScriptBash")
    davra_agent.agentFunctionRunScriptBash({"script": "echo hello"})
    (parts, cwd) = commands[-1]
    assert parts[-3:] == ["bash", "-x", davra_agent.currentFunctionDir + "/script.sh"]
    assert cwd == davra_agent.currentFunctionDir


def test_script_file_permissions(monkeypatch, apps):
    seen = {}

    def fakeRun(parts, timeout, cwd=None):
        seen["file"] = stat.S_IMODE(os.stat(parts[-1]).st_mode)
        seen["dir"] = stat.S_IMODE(os.stat(cwd).st_mode)
        return (0, "")
    monkeypatch.setattr(davra_lib, "runCommandWithTimeout", fakeRun)
    startFunction("agent-action-runScriptBash")
    davra_agent.agentFunctionRunScriptBash({"script": "true"})
    assert seen == {"file": 0o700, "dir": 0o700}


# App install (H1)

def tarball(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for (name, content) in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class FakeDownload(object):
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.body


@pytest.fixture
def download(monkeypatch):
    requested = []

    def serve(body):
        monkeypatch.setattr(davra_agent.requests, "get",
                            lambda url, **kw: requested.append((url, kw)) or FakeDownload(body))
        return requested
    return serve


def test_app_install_runs_installer_without_shell_or_credentials(download, commands, apps):
    body = tarball({"install.sh": b"echo installed\r\n"})
    requested = download(body)
    startFunction("agent-action-pushAppWithInstaller")
    davra_agent.agentFunctionPushAppWithInstaller({
        "Installation File": "https://files.example/app.tar.gz; rm -rf /",
        "SHA-256": hashlib.sha256(body).hexdigest()})
    (parts, cwd) = commands[-1]
    assert parts == ["bash", "./install.sh"]
    with open(os.path.join(cwd, "install.sh"), "rb") as f:
        assert f.read() == b"echo installed\n"
    assert "headers" not in requested[0][1]


def test_app_install_refuses_http(download, commands, apps):
    download(tarball({"install.sh": b""}))
    startFunction("agent-action-pushAppWithInstaller")
    davra_agent.agentFunctionPushAppWithInstaller({"Installation File": "http://files.example/app.tar.gz"})
    assert commands == []


def test_app_install_refuses_checksum_mismatch(download, commands, apps):
    download(tarball({"install.sh": b""}))
    startFunction("agent-action-pushAppWithInstaller")
    davra_agent.agentFunctionPushAppWithInstaller({"Installation File": "https://files.example/app.tar.gz",
                                                   "SHA-256": "00" * 32})
    assert commands == []


def test_app_install_refuses_path_traversal(download, commands, apps, installDir):
    download(tarball({"install.sh": b"", "../../escaped.txt": b"x"}))
    startFunction("agent-action-pushAppWithInstaller")
    davra_agent.agentFunctionPushAppWithInstaller({"Installation File": "https://files.example/app.tar.gz"})
    assert commands == []
    assert not os.path.exists(os.path.join(os.path.dirname(installDir), "escaped.txt"))


# Platform mqtt transport (C4)

def test_platform_mqtt_requires_tls(monkeypatch, config):
    created = []
    monkeypatch.setattr(davra_agent, "newMqttClient", lambda: created.append(1))
    config["mqttBrokerServerHost"] = "mqtt.davra.com"
    config["mqttBrokerServerUseTLS"] = False
    davra_agent.mqttConnectToServer()
    assert created == []


def test_platform_mqtt_uses_verified_tls_context(monkeypatch, config):
    class Client(object):
        def __getattr__(self, name):
            return lambda *a, **kw: calls.append((name, a, kw))
    calls = []
    monkeypatch.setattr(davra_agent, "newMqttClient", Client)
    config["mqttBrokerServerHost"] = "mqtt.davra.com"
    davra_agent.mqttConnectToServer()
    context = [c for c in calls if c[0] == "tls_set_context"][0][1][0]
    assert context.check_hostname is True
    assert [c for c in calls if c[0] == "connect"][0][2]["port"] == 8883
