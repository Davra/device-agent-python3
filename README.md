# device-agent-python3
Davra Device Agent in python3

This is the source repository. When built, the artifact for installing on devices is at:
downloads.davra.com/agents/device-agent-python3/main/davra-agent.tar.gz


## Installation on a device:

```
curl -LO downloads.davra.com/agents/device-agent-python3/main/davra-agent.tar.gz
tar -xvf davra-agent.tar.gz
sudo davra-agent/install.sh
```


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
