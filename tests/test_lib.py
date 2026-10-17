import base64
import hashlib
import os
import ssl
import stat

import pytest

import davra_lib
from conftest import TEST_TOKEN


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_redacted_config_hides_secrets_without_changing_config(config):
    redacted = davra_lib.redactedConfig()
    assert redacted["apiToken"] == davra_lib.redactedValue
    assert redacted["localMqttAgentPassword"] == davra_lib.redactedValue
    assert redacted["UUID"] == config["UUID"]
    assert davra_lib.conf["apiToken"] == TEST_TOKEN


def test_config_file_is_owner_only(config):
    davra_lib.upsertConfigurationItem("heartbeatInterval", 300)
    assert mode(davra_lib.agentConfigFile) == 0o600


def test_configured_event_sent_to_server_has_no_token(config):
    davra_lib.reportDeviceConfigurationToServer()
    bodies = [kw["data"] for (_, _, kw) in davra_lib.sentRequests if kw.get("data")]
    assert bodies
    assert all(TEST_TOKEN not in body for body in bodies)


def test_log_is_single_line_without_token_and_not_world_readable(config, logFile):
    if os.path.exists(logFile):
        os.remove(logFile)
    davra_lib.log("line one\nforged line " + TEST_TOKEN)
    with open(logFile) as f:
        content = f.read()
    assert TEST_TOKEN not in content
    assert content.count("\n") == 1
    assert mode(logFile) & 0o007 == 0


def test_only_info_and_above_logs_are_sent_to_server(config):
    davra_lib.log("debug message", "DEBUG")
    assert not any(url.endswith("/api/v1/logs") for (_, url, _) in davra_lib.sentRequests)
    davra_lib.log("error message", "ERROR")
    assert any(url.endswith("/api/v1/logs") for (_, url, _) in davra_lib.sentRequests)


def test_token_is_only_sent_to_the_davra_server(config):
    davra_lib.httpGet("http://ip-api.com/json")
    davra_lib.httpGet("https://tenant.davra.com.evil.example/api")
    davra_lib.httpGet("https://tenant.davra.com/api/v1/devices")
    headers = [kw["headers"] for (_, _, kw) in davra_lib.sentRequests]
    assert "Authorization" not in headers[0]
    assert "Authorization" not in headers[1]
    assert headers[2]["Authorization"] == "Bearer " + TEST_TOKEN


def test_server_url_must_be_https_unless_insecure_dev(config):
    davra_lib.validateServerUrl("https://tenant.davra.com")
    with pytest.raises(ValueError):
        davra_lib.validateServerUrl("http://tenant.davra.com")
    with pytest.raises(ValueError):
        davra_lib.validateServerUrl("file:///etc/passwd")
    config["insecureDevMode"] = True
    davra_lib.validateServerUrl("http://localhost:8080")
    with pytest.raises(ValueError):
        davra_lib.validateServerUrl("ftp://localhost")


def test_tls_context_verifies_and_requires_tls12():
    context = davra_lib.buildTlsContext()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_run_command_uses_no_shell():
    with pytest.raises(TypeError):
        davra_lib.runCommandWithTimeout("echo hi; id", 5)
    assert davra_lib.runCommandWithTimeout(["echo", "a; id"], 5) == (0, "a; id\n")
    assert davra_lib.runCommandWithTimeout(["sleep", "5"], 1)[0] == -1


def test_working_directories_are_root_only_and_inside_installation(installDir):
    workDir = os.path.join(installDir, "currentFunction")
    davra_lib.provideFreshDirectory(workDir)
    assert mode(workDir) == 0o700
    with pytest.raises(ValueError):
        davra_lib.provideFreshDirectory("/tmp/somewhere-else")
    with pytest.raises(ValueError):
        davra_lib.provideFreshDirectory(installDir)


def test_mosquitto_password_hash_format():
    hashed = davra_lib.mosquittoPasswordHash("secret")
    (_, version, iterations, salt, digest) = hashed.split("$")
    assert version == "7" and iterations == "101"
    expected = hashlib.pbkdf2_hmac("sha512", b"secret", base64.b64decode(salt), 101, 64)
    assert base64.b64decode(digest) == expected
