# Davra Device Agent (Python 3)

> **Open Source — Unsupported Release**
>
> This repository has been open-sourced for community reference and learning purposes. **Davra does not provide support, bug fixes, security patches, or any maintenance for this codebase in its current state.** Pull requests and issues will not be actively reviewed or merged. Use at your own risk. If you build on or fork this project, you assume full responsibility for ongoing maintenance and security.

---

## Overview

The Davra Device Agent is a Python 3 daemon designed to run on Linux edge devices. It connects the device to the Davra IoT platform, enabling:

- Remote job and function execution triggered from the Davra server
- Continuous metric reporting (CPU, RAM, uptime, and custom application metrics)
- Bidirectional MQTT messaging between the device and the platform
- A local SDK (`davra_sdk.py`) for application developers to write device apps that integrate with the agent

The agent communicates with the Davra platform over MQTT and HTTP REST. Device applications communicate with the agent over a local MQTT broker (Mosquitto on `127.0.0.1:1883`).

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Davra Platform                    │
│          (REST API + Remote MQTT Broker)             │
└───────────────────────┬─────────────────────────────┘
                        │ HTTPS / MQTT(S)
┌───────────────────────▼─────────────────────────────┐
│                  Device Agent                        │
│              (davra_agent.py)                        │
│                                                      │
│  - Job polling & execution                           │
│  - Heartbeat & metric reporting                      │
│  - Built-in agent functions                          │
│  - Remote MQTT client (to platform)                  │
│  - Local MQTT client (to device apps)                │
└───────────┬──────────────────────────────────────────┘
            │ MQTT (127.0.0.1:1883)
┌───────────▼──────────────────────────────────────────┐
│           Device Applications                        │
│       (using davra_sdk.py)                           │
│                                                      │
│  - Register capabilities                             │
│  - Send metrics and events                           │
│  - Receive function calls from platform              │
└──────────────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|---|---|
| `davra_agent.py` | Main agent daemon. Manages jobs, MQTT connections, heartbeats. |
| `davra_sdk.py` | Public SDK for device application developers. |
| `davra_lib.py` | Shared utilities: HTTP, logging, config, system info. |
| `davra_setup.py` | Interactive setup script run during installation. |
| `install.sh` | Installs system dependencies, the Python virtual environment and the systemd service. |
| `uninstall.sh` | Stops service and deactivates agent executables. |
| `build.sh` | Builds the distributable `davra-agent.tar.gz` artifact. |
| `requirements.in` / `requirements.txt` | Python dependencies: direct, and fully pinned with hashes. |

---

## Installation

### Prerequisites

- Linux OS with systemd
- **Python 3.12 or later** (Ubuntu 24.04 LTS, Debian 13 or later). The installer stops on older versions.
- `apt`-based package manager (for automated dependency installation)
- A Davra server reachable over **HTTPS**, and its MQTT broker over **MQTTS** (port 8883)

### Install from the Pre-built Artifact

```bash
curl -LO https://downloads.davra.com/agents/device-agent-python3/main/davra-agent.tar.gz
sha256sum -c build_checksum.txt   # compare with the published checksum
tar -xvf davra-agent.tar.gz
sudo davra-agent/install.sh --server https://myplatform.davra.com --token-file /root/davra-token
```

The installer will:

1. Check for Python 3.12 or later
2. Create `/usr/bin/davra/` and copy the agent files there (root-owned, not writable by others)
3. Install system packages: `python3-venv`, `mosquitto`
4. Create the `davra-apps` group (see [Device apps and the local broker](#device-apps-and-the-local-broker))
5. Create a virtual environment at `/usr/bin/davra/.venv` and install the hash-pinned Python dependencies from `requirements.txt`
6. Run `davra_setup.py` to configure the device
7. Install and enable the `davra_agent` systemd service

### Setup (`davra_setup.py`)

Setup takes its values from these options, prompting only for anything missing (and only when run interactively):

| Option | Purpose |
|---|---|
| `--server URL` | Davra server URL. Must be `https://`. |
| `--token-file FILE` | File containing the device API token. Alternatively set `DAVRA_API_TOKEN`, or type it at the (hidden) prompt. |
| `--insecure-dev` | **Development only.** Allows an `http://` server and MQTT without TLS. Reported in the agent heartbeat. Re-running setup without it switches it off. |
| `--allow-anonymous-local-mqtt` | Temporarily lets device apps built with an older SDK use the local broker without credentials. |
| `--secure-mqtt` | Only (re)configure the local MQTT broker, then exit. |
| `--no-service-restart` | Do not restart the agent service at the end of setup. |

`--token` is still accepted but deprecated: a token on the command line is visible to other users on the device.

Setup will:

- Verify the server is reachable over HTTPS and that the token belongs to a device
- Auto-detect the MQTT broker address and configure MQTTS (TLS 1.2+, certificate and hostname verification)
- Set default `heartbeatInterval` (600 seconds) and `scriptMaxTime` (600 seconds)
- Create default metrics on the platform (`cpu`, `ram`, `uptime`)
- Secure the local MQTT broker with generated credentials
- Write configuration to `/usr/bin/davra/config.json` (readable by root only)

### Uninstall

```bash
sudo davra-agent/uninstall.sh
```

Stops the service and renames agent executables (preserves logs and configuration).

---

## Configuration

All agent configuration lives in `/usr/bin/davra/config.json`, which is readable by root only. Secrets in it (`apiToken`, `localMqttAgentPassword`) are never sent to the platform, logged or given to device apps; reports of the configuration show them as `***REDACTED***`.

| Key | Description | Default |
|---|---|---|
| `server` | Davra platform base URL (`https://`) | set during setup |
| `UUID` | Device UUID on the platform | set during setup |
| `apiToken` | Bearer token for the platform API and MQTT broker | set during setup |
| `heartbeatInterval` | Seconds between heartbeats and job polls (10–86400) | `600` |
| `scriptMaxTime` | Maximum seconds allowed for a script or function (1–86400) | `600` |
| `mqttBrokerServerHost` | Platform MQTT broker hostname | `mqtt.davra.com` |
| `mqttBrokerServerPort` | Platform MQTT broker port | `8883` |
| `mqttBrokerAgentHost` | Local MQTT broker address | `127.0.0.1` |
| `localMqttAgentPassword` | The agent's password on the local broker | generated by setup |
| `insecureDevMode` | Set by `--insecure-dev`. Never use in production. | absent |
| `capabilities` | Map of registered device capabilities | `{}` |

`agent-action-updateAgentConfig` cannot change connection or security settings: `server`, `apiToken`, `UUID`, `capabilities`, `insecureDevMode`, `service`, `agentRepository`, or any `mqtt*` / `localMqtt*` key.

### MQTT TLS Configuration

The connection to the platform MQTT broker always uses TLS 1.2 or later, with certificate and hostname verification. Only `--insecure-dev` allows a plain connection.

| Key | Type | Description |
|---|---|---|
| `mqttBrokerServerUseTLS` | boolean | `true`. Can only be `false` in insecure dev mode. |
| `mqttBrokerServerPort` | integer | `8883` |
| `mqttBrokerServerCaCert` | string | Path to a CA certificate file (uses the system trust store if omitted) |
| `mqttBrokerServerClientCert` | string | Path to a client certificate (for mutual TLS) |
| `mqttBrokerServerClientKey` | string | Path to the client private key (required when a client cert is set) |

The settings `mqttBrokerServerTlsVersion`, `mqttBrokerServerCertRequired` and `mqttBrokerServerVerifyHostname` from earlier versions are no longer supported. Setup removes them.

### Device apps and the local broker

The local Mosquitto broker listens on `127.0.0.1:1883` only, does not allow anonymous connections, and limits clients to the `/agent` topic. Setup writes:

| File | Contents |
|---|---|
| `/etc/mosquitto/conf.d/davra.conf` | Listener, authentication and ACL settings |
| `/etc/mosquitto/davra_passwd`, `/etc/mosquitto/davra_acl` | Password hashes and topic ACL |
| `/usr/bin/davra/app_mqtt_credentials.json` | The credentials device apps use. Readable by root and the `davra-apps` group. |

Device apps that run as a non-root user must be in the `davra-apps` group (`usermod -aG davra-apps <user>`).

Messages from device apps are treated as untrusted. Apps can register their own capabilities, send IoT data, request the (redacted) agent configuration and report the result of a function they were asked to run. They cannot run agent functions (`agent-action-*`): only the Davra platform can.

### Log File

Agent logs are written to `/var/log/davra_agent.log` (mode 640, group `adm`) with automatic rollover at 10 MB. Secrets are removed from log messages.

---

## Service Management

The agent runs as a systemd service named `davra_agent`.

```bash
# Check status
sudo systemctl status davra_agent

# Stop the agent
sudo systemctl stop davra_agent

# Start the agent
sudo systemctl start davra_agent

# View logs
sudo journalctl -u davra_agent -f
# or
tail -f /var/log/davra_agent.log
```

The service is configured to restart automatically (`Restart=always`, `RestartSec=5`) with a 50-second startup delay to allow network interfaces to initialize.

---

## Agent Capabilities

### Heartbeat & Metrics

Every `heartbeatInterval` seconds the agent:

- Sends a heartbeat MQTT message to all connected device apps
- Reports the following metrics to the platform:
  - `cpu` — current CPU usage (%)
  - `ram` — available RAM (MB)
  - `uptime` — device uptime string
  - `davra.agent.heartbeat` — event confirming agent is alive
- Polls the platform for pending jobs

Every `heartbeatInterval × 10` seconds the agent reports all registered device capabilities to the platform.

### Built-in Agent Functions

These functions can be triggered remotely from the Davra platform:

| Function Name | Description |
|---|---|
| `agent-action-rebootDevice` | Reboots the device |
| `agent-action-pushAppWithInstaller` | Downloads a `.tar.gz` from an `https://` URL and runs its `install.sh`. If the optional `SHA-256` parameter is supplied, the download must match it. |
| `agent-action-reportAgentConfig` | Sends current config as an event to the platform |
| `agent-action-updateAgentConfig` | Updates a single config key and syncs to platform |
| `agent-action-runScriptBash` | Executes an arbitrary bash script, as root, with a configurable timeout |

### Job Execution

1. Agent polls `GET /api/v1/jobs` for pending jobs
2. Writes job details to `/usr/bin/davra/currentJob/job.json`
3. Executes the referenced function (either built-in or via a registered app)
4. Monitors completion by watching the job file's `status` field
5. Reports results back via `PUT /api/v1/jobs/{jobUUID}/{deviceUUID}`
6. Fires a `davra.job.finished` event

---

## Device Application SDK (`davra_sdk.py`)

The SDK is for developers writing Python applications that run on the same device as the agent. Applications communicate with the agent over the local MQTT broker.

### Quick Start

```python
import davra_sdk

# Connect to the local agent
davra_sdk.connectToAgent("my-temperature-app")

# Wait until the agent is ready (returns True or raises on timeout)
davra_sdk.waitUntilAgentIsConnected(timeoutSeconds=30)

# Register a capability that the platform can invoke remotely
def measure_temperature(msg):
    temp = read_sensor()
    davra_sdk.sendMetricValue("temperature", temp)

davra_sdk.registerCapability(
    "myApp-measureTemperature",
    {"description": "Read temperature sensor", "functionParameters": {"sensor_id": "string"}},
    measure_temperature
)

# Optionally listen to all messages from the agent
def on_agent_message(msg):
    print("Agent says:", msg)

davra_sdk.listenToAllMessagesFromAgent(on_agent_message)

# Send a single metric
davra_sdk.sendMetricValue("temperature", 22.5)

# Send multiple metrics at once
davra_sdk.sendMultiMetricValues([{"temperature": 22.5}, {"humidity": 60}])

# Send a fully-formed IoT data record
davra_sdk.sendIotData({
    "name": "pressure",
    "value": 1013.25,
    "msg_type": "datum",
    "tags": {"location": "room1"}
})

# Request the current agent configuration
davra_sdk.retrieveConfigFromAgent()
config = davra_sdk.agentConfig  # Populated after the above call returns
```

### Credentials

The SDK reads the app credentials from `/usr/bin/davra/app_mqtt_credentials.json` automatically. Run the app as root or as a member of the `davra-apps` group.

### Connecting with TLS

To connect the SDK with TLS enabled, pass a `tlsConfig` dict to `connectToAgent`. TLS 1.2 or later with certificate and hostname verification is always used.

```python
import davra_sdk

tls_config = {
    "ca_certs": "/path/to/ca.crt",
    "port": 8883
}

davra_sdk.connectToAgent("my-app", useTls=True, tlsConfig=tls_config)
davra_sdk.waitUntilAgentIsConnected(timeoutSeconds=30)
```

### SDK API Reference

#### Connection

| Function | Description |
|---|---|
| `connectToAgent(nameOfApplication, useTls=False, tlsConfig=None)` | Initializes MQTT connection to the local agent. Must be called first. |
| `waitUntilAgentIsConnected(timeoutSeconds)` | Blocks until the agent sends its first heartbeat, or raises on timeout. |
| `retrieveConfigFromAgent()` | Requests the current configuration from the agent. Result available in `davra_sdk.agentConfig`. |

#### Capability Registration

| Function | Description |
|---|---|
| `registerCapability(capabilityName, capabilityDetails, functionToRun)` | Registers a named function that the platform can invoke on this device. `functionToRun` receives the raw MQTT message as its argument. |
| `listenToAllMessagesFromAgent(functionToCallForEachMessage)` | Subscribes a handler that receives every message sent by the agent. |

#### Sending Data

| Function | Description |
|---|---|
| `sendMetricValue(metricName, metricValue)` | Sends a single numeric metric to the platform via the agent. |
| `sendMultiMetricValues(metrics)` | Sends a list of `{metricName: value}` dicts in one call. |
| `sendIotData(dataToSend)` | Sends a fully-formed IoT data record (dict). Supports `msg_type: "datum"` or `"event"`, `tags`, `latitude`/`longitude`. |
| `sendMessageFromAppToAgent(msg)` | Sends a raw custom message to the agent over MQTT. |

#### Utilities

| Function | Description |
|---|---|
| `runCommandWithTimeout(command, timeout)` | Runs a command with a timeout and returns `(exitCode, stdout)`. Pass a list of arguments; a string runs in a shell, so only build it from trusted values. |
| `loadAppConfiguration(davraAppConfigFile)` | Loads a JSON config file for the application. |
| `loadAgentConfigurationFile()` | Deprecated; returns `{}`. Use `retrieveConfigFromAgent()`. |

#### State Properties

| Property | Description |
|---|---|
| `davra_sdk.agentConfig` | Dict of agent configuration, populated after `retrieveConfigFromAgent()`. |
| `davra_sdk.lastSeenAgent` | Epoch milliseconds when the agent last sent a heartbeat. |
| `davra_sdk.mqttBrokerAgentHost` | Local broker address (default `127.0.0.1`). Override before calling `connectToAgent` if needed. |

---

## IoT Data Format

### Metric (datum)

```json
{
  "UUID": "device-uuid",
  "name": "temperature",
  "value": 22.5,
  "msg_type": "datum",
  "timestamp": 1714123456000,
  "tags": { "location": "room1" },
  "latitude": 51.5074,
  "longitude": -0.1278
}
```

### Event

```json
{
  "UUID": "device-uuid",
  "name": "sensor.alert",
  "value": 1,
  "msg_type": "event",
  "timestamp": 1714123456000,
  "tags": { "severity": "warning" }
}
```

`timestamp` is milliseconds since Unix epoch. If omitted, the platform applies a server-side timestamp.

---

## Platform REST API (Reference)

The agent uses the following Davra platform endpoints. Requests to the Davra server use `Authorization: Bearer <apiToken>`; the token is never sent to any other host.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/user` | Verify device/token connectivity |
| `GET` | `/api/v1/devices/{UUID}` | Retrieve device record |
| `PUT` | `/api/v1/devices/{UUID}` | Update device labels and capabilities |
| `PUT` | `/api/v1/iotdata` | Send metrics and events |
| `GET` | `/api/v1/iotdata` | Query stored metrics/events |
| `PUT` | `/api/v1/logs` | Send log entries |
| `GET/PUT` | `/api/v1/jobs` | Poll for and report on jobs |
| `POST` | `/api/v1/iotdata/meta-data` | Create metric definitions |

---

## Building from Source

```bash
./build.sh
```

Produces:

- `build/davra-agent.tar.gz` — installable artifact
- `build/build_version.txt` — agent version string
- `build/build_checksum.txt` — SHA-256 checksum of the artifact
- `build/build_jenkins.txt` — CI build metadata

---

## Dependencies

### Python Packages

Direct dependencies are listed in `requirements.in`. `requirements.txt` pins every package, including transitive ones, by version and hash, and is generated with:

```bash
uv pip compile davra-agent/requirements.in --upgrade --generate-hashes --python-version 3.12 -o davra-agent/requirements.txt
```

| Package | Purpose |
|---|---|
| `paho-mqtt` 2.x | MQTT client (local and platform). The SDK also works with paho-mqtt 1.x. |
| `requests` | HTTP REST client |

### System Packages

- `python3` (3.12+) and `python3-venv`
- `mosquitto` (local MQTT broker)
- `systemd`

---

## Security Model

- The agent runs as root because it runs jobs for the platform: scripts, app installs and reboots. Anyone who can create jobs for a device on the platform can run commands on it as root, so platform access control is part of the device's security.
- All traffic to the platform uses HTTPS and MQTTS (TLS 1.2+, verified certificates and hostnames).
- The device API token is stored in `/usr/bin/davra/config.json` (root only). It is sent only to the configured Davra server, and is never logged, reported in events or given to device apps.
- Agent functions can only be started by the platform. Device apps on the local broker are authenticated and cannot run them.
- Working files (`currentJob`, `currentFunction`) are root only. Scripts are written with exclusive-create and run straight away.
- Application packages are downloaded over HTTPS without credentials, size-limited, optionally SHA-256 verified and extracted with path-traversal protection.
- Protect any client key files with strict permissions (`chmod 600`) and keep certificates current.

## Upgrading from 2.0.x

Re-run `install.sh` from the new bundle. Note these changes:

- Python 3.12 or later is required, and dependencies are installed in a virtual environment rather than system-wide. Device apps which relied on the agent installing `requests` or `paho-mqtt` system-wide must install them themselves.
- An `http://` server URL, or MQTT without TLS, is refused. Change `server` in `config.json` to `https://` before upgrading, or use `--insecure-dev` in development only.
- The local broker now requires credentials. Device apps must use the updated `davra_sdk.py` and run as root or in the `davra-apps` group. `--allow-anonymous-local-mqtt` keeps older apps working while they are updated.
- Device apps can no longer send `runFunctionOnAgent`.
- The installation event no longer includes a latitude/longitude estimate (setup no longer calls a GeoIP service).

---

## Known Limitations

- Unit tests are in `tests/` (`python -m pytest tests`). There are no end-to-end tests against a live platform.
- Application packages are not signed; the SHA-256 check is optional.
- All configuration is file-based.

---

## License

See `LICENSE` file for terms. This software is provided as-is. Davra accepts no liability for its use.

---

> **Reminder:** This project is provided as an open-source reference only. **Davra does not offer support, maintenance, or security updates for this repository.** Community contributions are welcome but will not be actively reviewed.
