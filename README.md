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
                        │ HTTPS / MQTTS
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
| `install.sh` | Installs system dependencies, Python packages, and systemd service. |
| `uninstall.sh` | Stops service and deactivates agent executables. |
| `build.sh` | Builds the distributable `davra-agent.tar.gz` artifact. |
| `requirements.txt` | Python package dependencies. |

---

## Installation

### Prerequisites

- Linux OS with systemd
- Python 3.x
- `curl`
- `apt`-based package manager (for automated dependency installation)

### Install from the Pre-built Artifact

```bash
curl -LO downloads.davra.com/agents/device-agent-python3/main/davra-agent.tar.gz
tar -xvf davra-agent.tar.gz
sudo davra-agent/install.sh
```

The installer will:

1. Create `/usr/bin/davra/` and copy agent files there
2. Install system packages: `curl`, `python3`, `python3-pip`, `mosquitto`
3. Install Python dependencies from `requirements.txt`
4. Run `davra_setup.py` to interactively configure the device
5. Install and enable the `davra_agent` systemd service

### Interactive Setup (`davra_setup.py`)

During installation you will be prompted for:

- **Davra server URL** — e.g. `https://myplatform.davra.com`
- **API token** — Bearer token for the device

Setup will then:

- Verify connectivity to the server
- Auto-detect the MQTT broker address
- Set default `heartbeatInterval` (600 seconds) and `scriptMaxTime` (600 seconds)
- Create default metrics on the platform (`cpu`, `ram`, `uptime`)
- Write configuration to `/usr/bin/davra/config.json`

### Uninstall

```bash
sudo davra-agent/uninstall.sh
```

Stops the service and renames agent executables (preserves logs and configuration).

---

## Configuration

All agent configuration lives in `/usr/bin/davra/config.json`.

| Key | Description | Default |
|---|---|---|
| `server` | Davra platform base URL | set during setup |
| `UUID` | Device UUID on the platform | set during setup |
| `apiToken` | Bearer token for API/MQTT auth | set during setup |
| `heartbeatInterval` | Seconds between heartbeats and job polls | `600` |
| `scriptMaxTime` | Maximum seconds allowed for a script or function | `600` |
| `mqttBrokerServerHost` | Remote MQTT broker hostname | `mqtt.davra.com` |
| `mqttBrokerAgentHost` | Local MQTT broker address | `127.0.0.1` |
| `mqttRestrictions` | Set to `"localhost"` to restrict local broker to loopback | `"localhost"` |
| `capabilities` | Map of registered device capabilities | `{}` |

### Log File

Agent logs are written to `/var/log/davra_agent.log` with automatic rollover at 10 MB.

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

The service is configured to restart automatically (`Restart=always`, `RestartSec=5`) with a 50-second startup delay to allow network interfaces to initialise.

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
| `agent-action-pushAppWithInstaller` | Downloads a `.tar.gz` from a URL and runs its `install.sh` |
| `agent-action-pushAppSnap` | Snap package installation (stub — not fully implemented) |
| `agent-action-reportAgentConfig` | Sends current config as an event to the platform |
| `agent-action-updateAgentConfig` | Updates a single config key and syncs to platform |
| `agent-action-runScriptBash` | Executes an arbitrary bash script with a configurable timeout |

### Job Execution

1. Agent polls `GET /api/v1/jobs` for pending jobs
2. Writes job details to `/usr/bin/davra/currentJob/job.json`
3. Executes the referenced function (either built-in or via a registered app)
4. Monitors completion by watching the job file's `status` field
5. Reports results back via `PUT /api/v1/jobs/{jobUUID}/{deviceUUID}`
6. Fires a `davra.job.finished` event

---

## Device Application SDK (`davra_sdk.py`)

The SDK is intended for developers writing Python applications that run on the same device as the agent. Applications communicate with the agent over the local MQTT broker.

### Quick Start
Also within this repository is the davra_sdk.py which is designed for application developers to use when writing their own device apps.

## MQTT TLS/SSL Support

The agent now supports secure MQTT connections (MQTTS) for enhanced security. TLS/SSL can be configured during setup or by manually editing the configuration file.

### Configuration Options

The following configuration parameters are available in `config.json`:

- **mqttBrokerServerUseTLS** (boolean): Enable/disable TLS for MQTT connection
- **mqttBrokerServerPort** (integer): MQTT port (default: 1883 for plain, 8883 for TLS)
- **mqttBrokerServerCaCert** (string, optional): Path to CA certificate file (uses system default if not specified)
- **mqttBrokerServerClientCert** (string, optional): Path to client certificate file (for mutual TLS authentication)
- **mqttBrokerServerClientKey** (string, optional): Path to client private key file (required if client cert is provided)
- **mqttBrokerServerTlsVersion** (string, optional): TLS version to use (TLSv1.2, TLSv1.3, etc.) - **Leave unset for auto-negotiation (recommended)**
- **mqttBrokerServerCertRequired** (boolean): Require certificate verification (default: true)
- **mqttBrokerServerVerifyHostname** (boolean): Verify certificate hostname matches server (default: true)

**Note:** The TLS version will auto-negotiate to the highest version supported by both client and server if not specified. This is the recommended configuration for maximum compatibility.

### Setup with TLS

During setup, you'll be prompted to configure TLS settings:

```bash
sudo python3 davra_setup.py
```

The setup will ask:
1. Whether to enable TLS/SSL for MQTT
2. MQTT port (defaults to 8883 for TLS)
3. Path to CA certificate (optional)
4. Path to client certificate and key (optional, for mutual TLS)
5. TLS version preference
6. Certificate verification options

### Manual Configuration

You can also manually edit `/usr/bin/davra/config.json` to configure TLS:

```json
{
  "mqttBrokerServerHost": "mqtt.davra.com",
  "mqttBrokerServerUseTLS": true,
  "mqttBrokerServerPort": 8883,
  "mqttBrokerServerCaCert": "/path/to/ca.crt",
  "mqttBrokerServerTlsVersion": "TLSv1.2",
  "mqttBrokerServerCertRequired": true,
  "mqttBrokerServerVerifyHostname": true
}
```

For mutual TLS authentication, add:

```json
{
  "mqttBrokerServerClientCert": "/path/to/client.crt",
  "mqttBrokerServerClientKey": "/path/to/client.key"
}
```

### SDK Usage with TLS

Device applications using `davra_sdk.py` can connect with TLS:

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

### SDK API Reference

#### Connection

| Function | Description |
|---|---|
| `connectToAgent(nameOfApplication)` | Initialises MQTT connection to the local agent. Must be called first. |
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
| `runCommandWithTimeout(command, timeout)` | Runs a shell command string with a timeout. Returns stdout as a string. |
| `loadAppConfiguration(davraAppConfigFile)` | Loads a JSON config file for the application. |
| `loadAgentConfigurationFile()` | Reads the agent config from `/usr/bin/davra/config.json`. |

#### State Properties

| Property | Description |
|---|---|
| `davra_sdk.agentConfig` | Dict of agent configuration, populated after `retrieveConfigFromAgent()`. |
| `davra_sdk.lastSeenAgent` | Epoch milliseconds when the agent last sent a heartbeat. |
| `davra_sdk.mqttBrokerAgentHost` | Local broker address (default `127.0.0.1`). Override before calling `connectToAgent` if needed. |
| `davra_sdk.useAdvancedMqttAuthorisation` | Set to `True` to enable MQTT username/password auth using UUID and API token. |

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

The agent uses the following Davra platform endpoints. All requests use `Authorization: Bearer <apiToken>`.

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
- `build/build_checksum.txt` — MD5 checksum of the artifact
- `build/build_jenkins.txt` — CI build metadata

---

## Dependencies

### Python Packages

| Package | Version | Purpose |
|---|---|---|
| `paho-mqtt` | 1.4.0 | MQTT client (local and remote) |
| `requests` | 2.20.0 | HTTP REST client |
| `jsonschema` | 3.0.1 | JSON validation |
| `python-dateutil` | 2.6.1 | Date/time utilities |
| `six` | 1.15.0 | Python 2/3 compatibility shim |
| `uuid` | — | UUID generation |

### System Packages

- `python3` / `python3-pip`
- `mosquitto` (local MQTT broker)
- `curl`
- `systemd`

---

## Security Considerations

> **Note:** This codebase has not been audited for production security hardening. Review carefully before deploying in sensitive environments.

- API tokens are stored in plaintext in `/usr/bin/davra/config.json`. Ensure appropriate file permissions.
- The `agent-action-runScriptBash` function executes arbitrary bash scripts sent from the platform. Trust in the platform's access controls is assumed.
- The local MQTT broker is restricted to `127.0.0.1` by default (`mqttRestrictions: "localhost"`). Changing this exposes it to the network.
- MQTT authentication (device UUID + API token) is optional and disabled by default. Enable via `useAdvancedMqttAuthorisation`.
- Script execution is bounded by `scriptMaxTime`, but input to scripts is not sanitised by the agent.

---

## Known Limitations

- No automated test suite is included in this repository.
- `agent-action-pushAppSnap` (snap package installation) is a stub and not implemented.
- All configuration is file-based; there is no environment variable support.
- The `six` library and some Python 2 compatibility patterns remain from an earlier version.
- Dependencies in `requirements.txt` are pinned to older versions and may contain known vulnerabilities.

---

## License

See `LICENSE` file for terms. This software is provided as-is. Davra accepts no liability for its use.

---

> **Reminder:** This project is provided as an open-source reference only. **Davra does not offer support, maintenance, or security updates for this repository.** Community contributions are welcome but will not be actively reviewed.
# Connect with TLS enabled
tlsConfig = {
    "ca_certs": "/path/to/ca.crt",
    "tls_version": "TLSv1.2",
    "cert_required": True,
    "verify_hostname": True,
    "port": 8883
}

davra_sdk.connectToAgent("MyApp", useTls=True, tlsConfig=tlsConfig)
```

### Security Recommendations

1. **Always use TLS in production** environments
2. **Keep certificates up to date** and monitor expiration dates
3. **Use certificate verification** (mqttBrokerServerCertRequired: true)
4. **Verify hostnames** (mqttBrokerServerVerifyHostname: true)
5. **Use TLSv1.2 or higher** for better security
6. **Protect private keys** - ensure proper file permissions (chmod 600)
7. **Use mutual TLS** when possible for additional authentication

### Backward Compatibility

The agent maintains full backward compatibility. If TLS settings are not configured, the agent will use standard unencrypted MQTT connections on port 1883. 
