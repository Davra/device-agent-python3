# Establish connection with the Davra server
# Save the location of the Davra server to config.json for use by other programs
# Secure the mqtt broker on this device and install the agent as a service
#
# Usage: davra_setup.py [install|upgrade] [--server URL] [--token-file FILE]
#                       [--insecure-dev] [--allow-anonymous-local-mqtt]
#                       [--secure-mqtt] [--no-service-restart]
# The api token can also be supplied in the DAVRA_API_TOKEN environment variable,
# or typed when prompted. Passing it with --token is deprecated as it is visible to other users.
#
import argparse
import getpass
import grp
import json
import os
import shutil
import sys

import requests

import davra_lib as comDavra


configFilename = comDavra.agentConfigFile
mosquittoConfigFile = "/etc/mosquitto/conf.d/davra.conf"
mosquittoPasswordFile = "/etc/mosquitto/davra_passwd"
mosquittoAclFile = "/etc/mosquitto/davra_acl"
# Files written by earlier versions of the agent, which allowed anonymous access or held the api token
legacyMosquittoFiles = ("/etc/mosquitto/conf.d/localhost.conf", "/etc/mosquitto/conf.d/davra.txt")
# Group whose members (device app service users) may read the app mqtt credentials
appCredentialsGroup = "davra-apps"
maxPromptAttempts = 3

parser = argparse.ArgumentParser(description="Set up the Davra Device Agent")
parser.add_argument("mode", nargs="?", default="install", help="install or upgrade")
parser.add_argument("--server", help="Davra server URL, eg https://myName.davra.com")
parser.add_argument("--token-file", help="File containing the device API token")
parser.add_argument("--token", help=argparse.SUPPRESS) # Deprecated: visible on the command line
parser.add_argument("--insecure-dev", action="store_true", \
    help="DEVELOPMENT ONLY: allow http:// servers and mqtt without TLS")
parser.add_argument("--allow-anonymous-local-mqtt", action="store_true", \
    help="Allow device apps using an old SDK to connect to the device mqtt broker without credentials")
parser.add_argument("--secure-mqtt", action="store_true", help="Only (re)secure the device mqtt broker, then exit")
parser.add_argument("--no-service-restart", action="store_true", help="Do not restart the agent service")
args, unknownArgs = parser.parse_known_args()

isInteractive = sys.stdin.isatty()

print("Running setup of Davra Agent " + comDavra.davraAgentVersion + " (" + args.mode + ")")
if unknownArgs:
    print("Ignoring unknown arguments: " + str(unknownArgs))


# Load configuration if it already exists
comDavra.loadConfiguration()


def fail(message):
    print("ERROR: " + message)
    sys.exit(1)


def ask(prompt):
    if not isInteractive:
        return ""
    return input(prompt)


# Apply insecure dev mode, which is recorded in config and reported in the agent heartbeat
if args.insecure_dev:
    print("WARNING: --insecure-dev allows http:// and mqtt without TLS. Do not use in production.")
    comDavra.conf["insecureDevMode"] = True
elif comDavra.conf.get("insecureDevMode"):
    print("Insecure dev mode was set previously; disabling it now (use --insecure-dev to keep it).")
    comDavra.conf.pop("insecureDevMode")


# Write a file readable by root and (optionally) a group
def writeProtectedFile(path, content, groupName = None, mode = 0o600):
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as outfile:
        outfile.write(content)
    if groupName:
        try:
            shutil.chown(path + ".tmp", user="root", group=groupName)
            os.chmod(path + ".tmp", mode)
        except (LookupError, PermissionError) as e:
            print("WARNING: could not give group " + groupName + " access to " + path + ": " + str(e))
    os.replace(path + ".tmp", path)


def groupExists(groupName):
    try:
        grp.getgrnam(groupName)
        return True
    except KeyError:
        return False


# Set the security on the mqtt broker on this device: localhost only, no anonymous access,
# separate credentials for the agent and for device apps, and an ACL limiting them to the /agent topic
def setDeviceMqttBrokerSecurity():
    comDavra.log("Setting security on device mqtt broker")
    if not comDavra.conf.get("localMqttAgentPassword"):
        comDavra.conf["localMqttAgentPassword"] = comDavra.generatePassword()
        comDavra.saveConfiguration()
    appCredentials = {}
    if os.path.isfile(comDavra.appMqttCredentialsFile):
        with open(comDavra.appMqttCredentialsFile) as credentialsFile:
            appCredentials = json.load(credentialsFile)
    if appCredentials.get("username") != comDavra.localMqttAppUsername or not appCredentials.get("password"):
        appCredentials = { "username": comDavra.localMqttAppUsername, "password": comDavra.generatePassword() }
    appGroup = appCredentialsGroup if groupExists(appCredentialsGroup) else None
    writeProtectedFile(comDavra.appMqttCredentialsFile, json.dumps(appCredentials, indent=4), appGroup, 0o640)

    mosquittoGroup = "mosquitto" if groupExists("mosquitto") else None
    writeProtectedFile(mosquittoPasswordFile, \
        comDavra.localMqttAgentUsername + ":" + comDavra.mosquittoPasswordHash(comDavra.conf["localMqttAgentPassword"]) + "\n" \
        + comDavra.localMqttAppUsername + ":" + comDavra.mosquittoPasswordHash(appCredentials["password"]) + "\n", \
        mosquittoGroup, 0o640)
    acl = "# Managed by the Davra agent setup\n"
    if args.allow_anonymous_local_mqtt:
        acl += "topic readwrite /agent\n"
    acl += "user " + comDavra.localMqttAgentUsername + "\ntopic readwrite /agent\n" \
        + "user " + comDavra.localMqttAppUsername + "\ntopic readwrite /agent\n"
    writeProtectedFile(mosquittoAclFile, acl, mosquittoGroup, 0o640)
    if args.allow_anonymous_local_mqtt:
        print("WARNING: --allow-anonymous-local-mqtt lets any local process use the device mqtt broker. " \
            + "Update device apps to the current SDK and re-run setup without it.")
    with open(mosquittoConfigFile, 'w') as outfile:
        outfile.write("# Managed by the Davra agent setup\n" \
            + "listener 1883 127.0.0.1\n" \
            + "allow_anonymous " + ("true" if args.allow_anonymous_local_mqtt else "false") + "\n" \
            + "password_file " + mosquittoPasswordFile + "\n" \
            + "acl_file " + mosquittoAclFile + "\n")
    os.chmod(mosquittoConfigFile, 0o644)
    for legacyFile in legacyMosquittoFiles:
        if os.path.isfile(legacyFile):
            os.remove(legacyFile)
    comDavra.conf.pop("mqttRestrictions", None)
    comDavra.saveConfiguration()
    comDavra.runCommandWithTimeout(['systemctl', 'restart', 'mosquitto'], 30)
    return

if args.secure_mqtt:
    setDeviceMqttBrokerSecurity()
    sys.exit(0)


def configGetServer():
    if 'server' not in comDavra.conf:
        server = args.server
        for attempt in range(maxPromptAttempts):
            if not server:
                server = ask("Server location? (eg https://myName.davra.com) : ").strip()
            try:
                comDavra.validateServerUrl(server)
                break
            except ValueError as e:
                print(str(e))
                server = None
        if not server:
            fail("A valid https:// server URL is required (--server)")
        comDavra.conf['server'] = server.rstrip('/')
        comDavra.saveConfiguration()
    try:
        comDavra.validateServerUrl(comDavra.conf['server'])
    except ValueError as e:
        fail(str(e) + ". Edit " + configFilename + " to use https://, or run setup with --insecure-dev for development only.")
    # Confirm can reach the server
    print("Establishing connection to Davra server... ")
    try:
        r = requests.get(comDavra.conf['server'], timeout=comDavra.httpTimeout)
    except requests.RequestException as e:
        fail("Cannot reach server " + comDavra.conf['server'] + ": " + str(e))
    if(r.status_code == 200):
        print("Ok, can reach " + comDavra.conf['server'])
    else:
        fail("Cannot reach server. " + comDavra.conf['server'] + ' Response: ' + str(r.status_code))

configGetServer()


# The api token comes from config, a file, the environment or a prompt. Never echoed or logged.
def getApiTokenCandidate():
    if args.token_file:
        with open(args.token_file) as tokenFile:
            return tokenFile.read().strip()
    if os.environ.get("DAVRA_API_TOKEN"):
        return os.environ["DAVRA_API_TOKEN"].strip()
    if args.token:
        print("WARNING: --token is deprecated as the token is visible to other users on this device. " \
            + "Use --token-file or the DAVRA_API_TOKEN environment variable.")
        return args.token.strip()
    if isInteractive:
        print("You should have created this device on the user interface first then retrieved an API token for it.")
        return getpass.getpass("API Token of this device? : ").strip()
    return ""


# Requires the API token of a device
def configGetApiTokenOfDevice():
    for attempt in range(maxPromptAttempts):
        token = comDavra.conf.get('apiToken') or getApiTokenCandidate()
        if not token:
            fail("No API token supplied (--token-file, DAVRA_API_TOKEN or interactive prompt)")
        # Confirm the details supplied can make authenticated API call to server
        # Find the UUID of this device
        headers = {'Accept': 'application/json', 'Authorization': 'Bearer ' + token}
        print('Confirming device on server')
        try:
            r = requests.get(comDavra.conf['server'] + '/user', headers = headers, timeout=comDavra.httpTimeout)
        except requests.RequestException as e:
            fail("Cannot reach server to confirm device: " + str(e))
        responseContent = json.loads(r.content) if r.status_code == 200 and comDavra.isJson(r.content) else {}
        if("UUID" in responseContent and responseContent.get("type") == "DEVICE"):
            comDavra.conf['UUID'] = responseContent['UUID']
            comDavra.conf['apiToken'] = token
            comDavra.saveConfiguration()
            print("Device confirmed on server: " + comDavra.conf['UUID'])
            return
        print("ERROR: Issue with API token. It does not appear to be a valid token for a device. Response: " + str(r.status_code))
        comDavra.conf.pop('apiToken', None)
        if not isInteractive:
            break
    fail("Could not confirm the device API token with the server")

configGetApiTokenOfDevice()

# Make sure an existing config file (which may predate this version) is readable by root only
comDavra.saveConfiguration()

# heartbeatInterval is how many seconds between calling home
if('heartbeatInterval' not in comDavra.conf):
    comDavra.upsertConfigurationItem('heartbeatInterval', 600)


# scriptMaxTime is how many seconds between a script can run for before timing out
if('scriptMaxTime' not in comDavra.conf):
    comDavra.upsertConfigurationItem('scriptMaxTime', 600)


# agentRepository is where the artifacts for the agent are published
# should also have /build_version.txt to indicate the latest release version
if('agentRepository' not in comDavra.conf):
    comDavra.upsertConfigurationItem('agentRepository', 'TBD')

# What is the host of the MQTT Broker on Davra Server
if('mqttBrokerServerHost' not in comDavra.conf):
    # No configuration exists for mqtt
    # Make assumptions for the cloud based scenarios
    if ('davra.com' in comDavra.conf['server']):
        inputmqttborker = ask("MQTT Server location? (eg mqtt.davra.com) [default: mqtt.davra.com]: ")
        if not inputmqttborker.strip():
            inputmqttborker = "mqtt.davra.com"
        comDavra.upsertConfigurationItem('mqttBrokerServerHost', inputmqttborker.strip())
    elif ('eemlive.com' in comDavra.conf['server']):
        comDavra.upsertConfigurationItem('mqttBrokerServerHost', 'mqtt.eemlive.com')
    else:
        # Assume the same host as the Davra server but ignore scheme or port definition
        mqttBroker = comDavra.conf['server'].replace("http://", "").replace("https://", "").split(":")[0].split("/")[0]
        print('Setting mqttBroker ' + str(mqttBroker))
        comDavra.upsertConfigurationItem('mqttBrokerServerHost', mqttBroker)


# Configure MQTT TLS. TLS 1.2+ with certificate and hostname verification is required
# unless in insecure dev mode. Options from earlier versions which weakened TLS are removed.
def configureMqttTls():
    for legacyKey in ("mqttBrokerServerTlsVersion", "mqttBrokerServerCertRequired", "mqttBrokerServerVerifyHostname"):
        if legacyKey in comDavra.conf:
            print("Removing unsupported setting " + legacyKey)
            comDavra.conf.pop(legacyKey)
    comDavra.saveConfiguration()
    if comDavra.isInsecureDevMode() and 'mqttBrokerServerUseTLS' not in comDavra.conf:
        useTlsInput = ask("Enable TLS for MQTT connection? (Y/N) [default: Y]: ")
        useTls = useTlsInput.strip().upper() not in ['N', 'NO', 'FALSE', '0']
    elif comDavra.isInsecureDevMode():
        useTls = comDavra.conf['mqttBrokerServerUseTLS']
    else:
        useTls = True
    if comDavra.conf.get('mqttBrokerServerUseTLS') is False and useTls:
        print("MQTT TLS is now required; switching the MQTT connection to TLS.")
    comDavra.upsertConfigurationItem('mqttBrokerServerUseTLS', useTls)
    if not useTls:
        comDavra.upsertConfigurationItem('mqttBrokerServerPort', 1883)
        return
    if comDavra.conf.get('mqttBrokerServerPort') in (None, 1883):
        mqttPortInput = ask("MQTT Port? [default: 8883]: ").strip()
        comDavra.upsertConfigurationItem('mqttBrokerServerPort', int(mqttPortInput) if mqttPortInput.isdigit() else 8883)
    if 'mqttBrokerServerCaCert' not in comDavra.conf:
        caCertInput = ask("Path to CA certificate file? (leave empty to use system default): ").strip()
        if caCertInput:
            comDavra.upsertConfigurationItem('mqttBrokerServerCaCert', caCertInput)
    if 'mqttBrokerServerClientCert' not in comDavra.conf:
        clientCertInput = ask("Path to client certificate file? (leave empty if not using mutual TLS): ").strip()
        if clientCertInput:
            comDavra.upsertConfigurationItem('mqttBrokerServerClientCert', clientCertInput)
            clientKeyInput = ask("Path to client key file? : ").strip()
            if clientKeyInput:
                comDavra.upsertConfigurationItem('mqttBrokerServerClientKey', clientKeyInput)

configureMqttTls()


# Reload configuration inside library
comDavra.loadConfiguration()


# Create necessary metrics on server
comDavra.createMetricOnServer('cpu', '%', 'CPU usage')
comDavra.createMetricOnServer('uptime', 's', 'Time since reboot')
comDavra.createMetricOnServer('ram', '%', 'RAM usage')


# Confirm MQTT Broker on agent
if(comDavra.checkIsAgentMqttBrokerInstalled() == False):
    comDavra.logError('MQTT Broker not installed')
    comDavra.upsertConfigurationItem("mqttBrokerAgentHost", '')
else:
    comDavra.log('MQTT Broker installed and running')
    comDavra.upsertConfigurationItem("mqttBrokerAgentHost", '127.0.0.1')
    setDeviceMqttBrokerSecurity()



# Send an event to the server to inform it of the installation (without secrets)
dataToSend = {
    "UUID": comDavra.conf['UUID'],
    "name": "davra.agent.installed",
    "value": {
        "deviceConfig": comDavra.redactedConfig()
    },
    "msg_type": "event"
}
comDavra.log('Sending installation event to server: ' + comDavra.conf['server'])
comDavra.sendDataToServer(dataToSend)



# Install as a service so it continually runs
if('service' not in comDavra.conf):
    # By default, install as a service
    print('Using root permissions to install as service')
    shutil.copyfile(os.path.join(comDavra.installationDir, 'davra_agent.service'), '/lib/systemd/system/davra_agent.service')
    os.chmod('/lib/systemd/system/davra_agent.service', 0o644)
    comDavra.runCommandWithTimeout(['systemctl', 'daemon-reload'], 30)
    comDavra.runCommandWithTimeout(['systemctl', 'enable', 'davra_agent.service'], 30)
    comDavra.runCommandWithTimeout(['systemctl', 'restart', 'davra_agent.service'], 30)
    comDavra.saveConfiguration()
else:
    if(comDavra.conf['service'] == 'y' or comDavra.conf['service'] =='Y'):
        if not args.no_service_restart:
            print('Using root permissions to restart service')
            comDavra.runCommandWithTimeout(['systemctl', 'daemon-reload'], 30)
            comDavra.runCommandWithTimeout(['systemctl', 'restart', 'davra_agent.service'], 30)
        else:
            print("Will not restart the service now. Please do so manually")


print("Finished setup.")
