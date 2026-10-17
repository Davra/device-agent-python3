# Davra SDK for Device Applications
# Import this SDK to assist writing Device Applications
# so they can communicate with the Davra Agent, running on the device
#
import os
import time
import json
import sys, subprocess
from datetime import datetime
import ssl
# Use MQTT to communicate with the davra device agent
import paho.mqtt.client as mqtt


# CONFIG

# Where is the MQTT broker running on the agent
mqttBrokerAgentHost = '127.0.0.1'
# Credentials for device apps on the device mqtt broker, written by the agent setup.
# Readable by root and members of the davra-apps group.
appMqttCredentialsFile = "/usr/bin/davra/app_mqtt_credentials.json"
# Deprecated and ignored: apps authenticate with the app credentials above, never the device api token
useAdvancedMqttAuthorisation = False

# END CONFIG

lastSeenAgent = 0; # When was the agent last seen on the mqtt topic
agentConfig = {} # A cached copy of the config on the agent. Update it by calling retrieveConfigFromAgent
deviceApplicationName = "Unknown" # The name of this application. 

# Load App configuration, eg. config.txt file
# File format is key=value per line
def loadAppConfiguration(davraAppConfigFile = "config.txt"):
    appConfig = {}
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), davraAppConfigFile)) as data_file:
            for line in data_file:
                if "#" not in line and "=" in line:
                    (key, val) = line.strip().split("=")
                    appConfig[key] = val
        print(("Config file loaded as " + str(appConfig)))
    except Exception as e:
        print(('ERROR: Cannot read config.txt file ' + davraAppConfigFile + ":" + str(e)))
    return appConfig


###########################   Utilities


def getMilliSecondsSinceEpoch():
    return int(time.time() * 1000)


# Does the supplied string contain valid json
def isJson(myjson):
    try:
        json_object = json.loads(myjson)
    except Exception as e:
        return False
    return True


# Log a message
def log(log_msg):
    log_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_msg = str(log_msg)
    try:
        print((log_time + ": " + log_msg))  # Echo to stdout
    except:
        print("Error: Logging to file failed")


###########################   Connect to the MQTT Broker running on device

# The callback for when the client receives a CONNACK response from the broker on the device.
def mqttOnConnectDevice(client, userdata, flags, resultCode, properties = None):
    if(resultCode == 0):
        log("Mqtt Device Broker: Connected with result code " + str(resultCode))
    else:
        log('Mqtt Device Broker: Could not connect. userdata:' + str(userdata) + ", flags:" + str(flags))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("/agent")
    return 


# The callback for when a PUBLISH message is received from the broker on device.
# Triage incoming instructions from the Device Agent (or other apps) and call the application functions
# which they registered previously
def mqttOnMessageDevice(client, userdata, msg):
    global lastSeenAgent
    global agentConfig
    payload = str(msg.payload.decode('utf8'))
    if(isJson(payload) and isinstance(json.loads(payload), dict)):
        msg = json.loads(payload)
        # Is this message one which this application issued (ie heard itself)
        if("fromApp" in msg and msg["fromApp"] == deviceApplicationName):
            return
        log('Mqtt Device Broker: Received Mqtt message: ' + payload)
        # Is this a function message and did this app register that function as a capability it does
        if("functionName" in msg and msg["functionName"] in appCapabilityFunctions):
            # appCapabilityFunctions contains key/value pair of function name and the actual function to call
            appCapabilityFunctions[msg["functionName"]](msg)  
        # Is the message an Agent Heartbeat from the Agent
        if("agentHeartbeat" in msg and "fromAgent" in msg):
            lastSeenAgent = int(msg["agentHeartbeat"]) 
        # Is the message an Agent Configuration listing
        if("agentConfig" in msg and "fromAgent" in msg):
            agentConfig = msg["agentConfig"] 
        # Did the app register that it wanted to listen to all messages which occur, irrespective of capability
        if("allMessages" in appCapabilityFunctions):
            appCapabilityFunctions["allMessages"](msg)
    return
    

# Setup the MQTT client talking to the broker on the device  
# This means messages can be heard by this SDK and passed into the nominated function in the Application
# when they are visible on the mqtt topic  
mqttClientOfDevice = None
def connectToAgent(nameOfApplication, useTls=False, tlsConfig=None):
    global mqttClientOfDevice 
    global deviceApplicationName
    deviceApplicationName = nameOfApplication
    if(len(mqttBrokerAgentHost) > 3):
        mqttClientOfDevice = newMqttClient()
        mqttClientOfDevice.on_connect = mqttOnConnectDevice
        mqttClientOfDevice.on_message = mqttOnMessageDevice
        # The device mqtt broker requires the app credentials written by the agent setup
        appCredentials = loadAppMqttCredentials()
        if appCredentials:
            mqttClientOfDevice.username_pw_set(appCredentials["username"], appCredentials["password"])
            log('MQTT: Will connect to broker running on device as ' + appCredentials["username"])
        else:
            log('MQTT: No app credentials readable at ' + appMqttCredentialsFile + '. ' \
                + 'Run the app as root or as a member of the davra-apps group.')

        # Configure TLS if enabled: certificate and hostname verification are always on, TLS 1.2 minimum
        mqttPort = 8883 if useTls else 1883
        if tlsConfig and "port" in tlsConfig:
            mqttPort = tlsConfig["port"]

        if useTls:
            log('Configuring MQTT with TLS encryption')
            try:
                if tlsConfig is None:
                    tlsConfig = {}
                for insecureOption in ("tls_version", "cert_required", "verify_hostname"):
                    if insecureOption in tlsConfig:
                        log('Warning: tlsConfig "' + insecureOption + '" is no longer supported and is ignored')
                context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=tlsConfig.get("ca_certs") or None)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                if tlsConfig.get("certfile"):
                    context.load_cert_chain(certfile=tlsConfig["certfile"], keyfile=tlsConfig.get("keyfile") or None)
                mqttClientOfDevice.tls_set_context(context)
                log('MQTT TLS configuration completed successfully')
            except Exception as e:
                log('Error configuring MQTT TLS: ' + str(e))
                return False

        log('Starting to connect to MQTT broker running on device ' + mqttBrokerAgentHost + ':' + str(mqttPort) + ' (TLS: ' + str(useTls) + ')')
        mqttClientOfDevice.connect(mqttBrokerAgentHost, port=mqttPort)
        mqttClientOfDevice.loop_start() # Starts another thread to monitor incoming messages
        time.sleep(2)
        sendMessageFromAppToAgent({"connectToAgent": deviceApplicationName})
        return True
    else:
        log('No MQTT broker configured on device')
        return False


# Create an mqtt client which works with paho-mqtt 1.x and 2.x
def newMqttClient():
    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    return mqtt.Client()


# Load the credentials device apps use on the device mqtt broker. Returns None if not readable.
def loadAppMqttCredentials():
    try:
        with open(appMqttCredentialsFile) as data_file:
            credentials = json.load(data_file)
        if credentials.get("username") and credentials.get("password"):
            return credentials
    except (OSError, ValueError) as e:
        log('Cannot read app mqtt credentials ' + appMqttCredentialsFile + ': ' + str(e))
    return None


# Deprecated: the agent config file is readable by root only and holds secrets.
# Use retrieveConfigFromAgent() for the (redacted) agent configuration.
def loadAgentConfigurationFile():
    log('loadAgentConfigurationFile is deprecated. Use retrieveConfigFromAgent() instead.')
    return {}



###########################   Send Messages from Device Application to Device Agent

# msg should be a json object. Eg: {"message": "test from app"}
# A key "fromApp" will be added to indicate which application is issuing this message
# which allows all the listeners (mainly the agent) to know who is communicating
# and this will be stripped out from the message by the agent before processing
def sendMessageFromAppToAgent(msg):
    global mqttClientOfDevice
    msg['fromApp'] = deviceApplicationName 
    log('sendMessageFromAppToAgent: sending msg: ' + str(msg))
    mqttClientOfDevice.publish('/agent', json.dumps(msg))


# Send a simple metric reading to agent to forward to /api/v1/iotdata
def sendMetricValue(metricName, metricValue):
    dataToSend = {"name": metricName, "value": metricValue, "msg_type": "datum"}
    sendIotData(dataToSend)


# Send multiple metric items
def sendMultiMetricValues(metrics):
    dataToSend = []
    for metric in metrics:
        for metricName, metricValue in list(metric.items()):
            dataToSend.append({"name": metricName, "value": metricValue, "msg_type": "datum"})

    sendIotData(dataToSend)
    

# Send a datum to agent to forward to /api/v1/iotdata
# For a metric:
# Supply dataToSend like: {"name": "cpu", "value": 12, "msg_type": "datum", "tags": {"os": "linux"}}
# For an event:
# Supply dataToSend like: {"name": "davranetworks.alarm", "msg_type": "event"
# "value": {"UUID": "ABCD", "message": "door open", "severity": "WARN"}, 
# "tags": {"os": "linux"}}
def sendIotData(dataToSend):
    sendMessageFromAppToAgent({"sendIotData": json.dumps(dataToSend)})



# Announce to the agent that this app is able to do some actions or measurements
# This will make its way to the platform server. jobs may then call these capbilities to run
appCapabilityFunctions = {}
def registerCapability(capabilityName, capabilityDetails, capabilityFunctionToRun):
    global appCapabilityFunctions
    log('registerCapability: announce application capability to agent: ' + str(capabilityName))
    sendMessageFromAppToAgent({"registerCapability": capabilityName, "capabilityDetails": capabilityDetails})
    # Allow any application relying on this SDK to register their function as what will be called
    # when a message is received from the agent, via mqtt
    appCapabilityFunctions[capabilityName] = capabilityFunctionToRun

# If the app wiches to receive a copy of all messages which are seen on the mqtt topic, 
# it can nominate a callback function which will be called whenever any message is seen
def listenToAllMessagesFromAgent(functionToCallForEachMessage):
    global appCapabilityFunctions
    appCapabilityFunctions["allMessages"] = functionToCallForEachMessage


# Stay in a loop until a heartbeat signal is seen from the agent over mqtt
# Returns True if the agent was seen, False if not
def waitUntilAgentIsConnected(timeoutSeconds):
    global lastSeenAgent
    startListeningTime = getMilliSecondsSinceEpoch() - 5000
    while timeoutSeconds > 0:
        # The agent is available if an mqtt message arrived and updated lastSeenAgent variable
        if(lastSeenAgent > startListeningTime):
            # Now the agent is available, update the cache of agent config
            retrieveConfigFromAgent() 
            return True
        # Only every n seconds
        if(timeoutSeconds % 10 == 0):
            log('Waiting until agent is connected. Timeout ' + str(timeoutSeconds) + ". lastSeenAgent: " + str(lastSeenAgent))
        timeoutSeconds -= 1
        time.sleep(1)
    return False


# Make an mqtt message asking the agent to respond with its configuration
def retrieveConfigFromAgent():
    sendMessageFromAppToAgent({"retrieveConfigFromAgent": "true"})


# Execute a command and return the exit code and stdout
# command is a list of arguments (preferred, no shell) or a command line string which runs in a shell.
# Only pass strings built from trusted values: a shell string is open to command injection.
# scriptResponse is a tuple of (exitStatusCode, stdout)
def runCommandWithTimeout(command, timeout):
    timeout = int(timeout)
    log("Running command with timeout " + str(command) + " timeout:" + str(timeout))
    try:
        p = subprocess.run(command, shell=isinstance(command, str), text=True, \
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)  # nosec B602 - the app's own command
        log("command finished. Exit code: " + str(p.returncode))
        return (p.returncode, p.stdout)
    except subprocess.TimeoutExpired:
        log("command timed out after " + str(timeout) + "s")
        return (-1, None)
