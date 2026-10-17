# Utility functions for other programs
#
import base64
import copy
import hashlib
import json
import os
import os.path
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit

import requests

# Update this when anything changes in the agent
davraAgentVersion = "2_1_0"

# The minimum Python version the agent supports (current LTS distros: Ubuntu 24.04, Debian 13)
minimumPythonVersion = (3, 12)

# Locations can be overridden by environment variables (used by tests)
installationDir = os.environ.get("DAVRA_INSTALL_DIR", "/usr/bin/davra")
# Config file for the agent running on this device
agentConfigFile = installationDir + "/config.json"
# Credentials device apps use to connect to the local mqtt broker
appMqttCredentialsFile = installationDir + "/app_mqtt_credentials.json"
# Where logs are saved by default
logDir = os.environ.get("DAVRA_LOG_DIR", "/var/log")
logFile = logDir + "/davra_agent.log"
logFileMaxBytes = 10000000
# Flags to indicate cache entries
flagNewCapabilityReadyToReport = False

# Config keys which hold secrets. These are never sent off the device, logged or given to apps.
sensitiveConfigKeys = ("apiToken", "localMqttAgentPassword", "mqttBrokerServerClientKey")
redactedValue = "***REDACTED***"

# Usernames on the local mqtt broker running on the device
localMqttAgentUsername = "davra-agent"
localMqttAppUsername = "davra-app"

# Timeout (seconds) for http requests: (connect, read)
httpTimeout = (10, 20)


# Load configuration if it exists
conf = {}
def loadConfiguration():
    global conf
    try:
        if(os.path.isfile(agentConfigFile) is True):
            with open(agentConfigFile) as data_file:
                conf = json.load(data_file)
    except (OSError, ValueError) as e:
        print('ERROR: Cannot read config file ' + agentConfigFile + ': ' + str(e))
loadConfiguration()


def getConfiguration():
    return conf


# A copy of the configuration which is safe to send to the server, log or give to apps
def redactedConfig(configToRedact = None):
    redacted = copy.deepcopy(conf if configToRedact is None else configToRedact)
    for key in sensitiveConfigKeys:
        if key in redacted:
            redacted[key] = redactedValue
    return redacted


# Write a json file atomically, readable only by its owner (root)
def writeJsonFile(fileToWrite, content, mode = 0o600):
    targetDir = os.path.dirname(os.path.abspath(fileToWrite))
    fd, tmpPath = tempfile.mkstemp(dir=targetDir, prefix=".tmp-")
    try:
        with os.fdopen(fd, 'w') as outfile:
            json.dump(content, outfile, indent=4)
        os.chmod(tmpPath, mode)
        os.replace(tmpPath, fileToWrite)
    except BaseException:
        if os.path.exists(tmpPath):
            os.remove(tmpPath)
        raise


# Write the full config file to disk (owner read/write only, as it holds the api token)
def saveConfiguration():
    writeJsonFile(agentConfigFile, conf)


# Update (or insert) a configuration item with a value
def upsertConfigurationItem(itemKey, itemValue):
    global conf
    loadConfiguration()
    if(os.path.isfile(agentConfigFile) is True):
        # If this a new key or an alteration of the current config
        if((itemKey in conf) == False or conf[itemKey] != itemValue):
            # Update the item
            conf[itemKey] = itemValue
            saveConfiguration()
            # Update local cached version of configs
            loadConfiguration()
            # Report the new configuration to the server as an event
            reportDeviceConfigurationToServer()
    return


# Send the configuration file (without secrets) to the server as an event
def reportDeviceConfigurationToServer():
    dataToSend = {
        "UUID": conf['UUID'],
        "name": "davra.agent.configured",
        "value": {
            'deviceConfig': redactedConfig()
        },
        "msg_type": "event"
    }
    logInfo('Sending configuration file to server: ' + conf['server'])
    sendDataToServer(dataToSend)
    log("reportDeviceConfigurationToServer finished.")
    return

# Send a log message to the server
logSeveritiesForServer = ("ERROR", "WARN", "INFO")
sendingLogToServer = False
def logToServer(severity, message):
    global sendingLogToServer
    # Do not send log to server if severity not in the required set
    # or if this log was raised while already sending a log (avoid recursion)
    if(severity not in logSeveritiesForServer or sendingLogToServer or 'UUID' not in conf):
        return
    dataToSend = {
        "UUID": conf['UUID'],
        "name": "davra.log",
        "value": {
            "severity": severity,
            "message": message
        }
    }
    sendingLogToServer = True
    try:
        sendLogToServer(dataToSend)
    finally:
        sendingLogToServer = False
    return

# Send various severities of log messages with easy function names
def logDebug(log_msg):
    log(log_msg, "DEBUG")

def logInfo(log_msg):
    log(log_msg, "INFO")

def logWarning(log_msg):
    log(log_msg, "WARN")

def logError(log_msg):
    if isinstance(log_msg, bytes):
        log_msg = log_msg.decode('utf8', errors='replace')
    log(log_msg, "ERROR")


# Make a message safe to log: one line only and no secrets
def sanitiseLogMessage(log_msg):
    log_msg = str(log_msg).replace('\r', '\\r').replace('\n', '\\n')
    for key in sensitiveConfigKeys:
        secret = conf.get(key)
        if isinstance(secret, str) and len(secret) > 3:
            log_msg = log_msg.replace(secret, redactedValue)
    return log_msg


# Log a message to disk and console
def log(log_msg, severity = "DEBUG"):
    log_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_msg = sanitiseLogMessage(log_msg)
    try:
        print(log_time + ": " + log_msg) # Echo to stdout as well as the file
        if os.path.isfile(logFile) and os.path.getsize(logFile) > logFileMaxBytes:
            os.replace(logFile, logFile + ".old")
        fd = os.open(logFile, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        with os.fdopen(fd, 'a') as logfile:
            logfile.write(log_time + ": " + severity + ": " + log_msg + "\n")
        # Only send log to server if above the log level required
        logToServer(severity, log_msg)
    except Exception as e:
        print("Error: Logging to file failed " + str(e))


###########################   TRANSPORT SECURITY


# Insecure dev mode allows http and plain mqtt. Set only by "davra_setup.py --insecure-dev"
def isInsecureDevMode():
    return conf.get("insecureDevMode") is True


# Raise ValueError unless the url uses https (http is accepted only in insecure dev mode)
def validateServerUrl(url):
    scheme = urlsplit(str(url)).scheme.lower()
    if scheme == "https":
        return
    if scheme == "http" and isInsecureDevMode():
        return
    raise ValueError("Server URL must use https:// : " + str(url))


# Is the destination on the configured Davra server (same scheme, host and port)
def isDavraServerUrl(destination):
    if 'server' not in conf:
        return False
    server = urlsplit(conf['server'])
    target = urlsplit(str(destination))
    return (server.scheme, server.netloc.lower()) == (target.scheme, target.netloc.lower())


# Build a TLS context for mqtt: certificate and hostname verification always on, TLS 1.2 minimum
def buildTlsContext(caFile = None, certFile = None, keyFile = None):
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=caFile or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    if certFile:
        context.load_cert_chain(certfile=certFile, keyfile=keyFile or None)
    return context


###########################   HTTP


# Only requests to the Davra server carry the device api token
def getHeadersForRequests(destination = None):
    headersTmp = {'Accept' : 'application/json', \
        'Content-Type' : 'application/json'}
    if destination is None or isDavraServerUrl(destination):
        headersTmp['Authorization'] = 'Bearer ' + conf['apiToken']
    return headersTmp


def createMetricOnServer(metricName, metricUnits, metricDescription):
    contents = [ { "name": metricName, "label": metricName, "description": metricDescription, "semantics": "metric" } ]
    r = httpPost(conf['server'] + '/api/v1/iotdata/meta-data', contents)
    if(r.status_code == 200):
        log("Metric created on server: " + metricName)
    else:
        log("Failed to create metric on server: " + metricName + ' : ' + str(r.status_code))
    return


# When sending iot data, this is a shorter function to use then regular put
def sendDataToServer(dataToSend):
    responseFromServer = httpPut(conf['server'] + '/api/v1/iotdata', dataToSend)
    return(responseFromServer)

def sendLogToServer(dataToSend):
    responseFromServer = httpPut(conf['server'] + '/api/v1/logs', dataToSend)
    return(responseFromServer)


# For when a http request fails, use this to return a similar object
class emptyRequestsObject(object):
    status_code = 500
    content = ""


# Make a http request. Requests to anywhere other than the Davra server carry no credentials.
def httpRequest(method, destination, dataToSend = None):
    headers = getHeadersForRequests(destination)
    try:
        body = None if dataToSend is None else json.dumps(dataToSend)
        r = requests.request(method, destination, data=body, headers=headers, timeout=httpTimeout)
        if (r.status_code != 200):
            log("Issue while making http " + method + " to " + str(destination) + ": " + str(r))
        return(r)
    except Exception as e:
        log('Failed to make http ' + method + ': ' + str(destination) + " Error: " + str(e))
        return(emptyRequestsObject())


# Make a http request of type PUT
# Supply the destination API endpoint as string and the dataToSend as JSON object
def httpPut(destination, dataToSend):
    return httpRequest('PUT', destination, dataToSend)


# Make a http request of type POST
# Supply the destination API endpoint as string and the dataToSend as JSON object
def httpPost(destination, dataToSend):
    return httpRequest('POST', destination, dataToSend)


# Make a http request of type GET
# Supply the destination API endpoint as string
def httpGet(destination):
    return httpRequest('GET', destination)


# Send the device capabilities from config file up to server at /api/v1/devices
def reportDeviceCapabilities():
    dataToSend = { "capabilities": conf["capabilities"] }
    log('Reporting device capabilities to server ' + str(dataToSend))
    r = httpPut(conf['server'] + '/api/v1/devices/' + conf["UUID"], dataToSend)
    if (r.status_code == 200):
        log('Reported device capabilities to server ' + str(r.content))
        return(r.status_code)
    else:
        log("Issue while reporting capabilities to server. " + str(r.status_code))
        log(r.content)
        return(r.status_code)


# Update (or insert) a configuration item with a capability for this device
def registerDeviceCapability(itemKey, itemValue):
    logInfo('registerDeviceCapability ' + str(itemKey) + ': ' + str(itemValue))
    listCapabilities = conf["capabilities"] if "capabilities" in conf else {}
    # If the capability was already known (and in the config.info)
    if((itemKey in listCapabilities) == False or listCapabilities[itemKey] != itemValue):
        log('registerDeviceCapability : this is a new capability')
        listCapabilities[itemKey] = itemValue
        upsertConfigurationItem("capabilities", listCapabilities)
        reportDeviceCapabilities()
    return


# Delete a configuration item of a capability for this device
def unregisterDeviceCapability(itemKey):
    logInfo('unregisterDeviceCapability ' + str(itemKey))
    listCapabilities = conf["capabilities"]
    # If the capability was already known (and in the config.info)
    if((itemKey in listCapabilities) == True):
        listCapabilities.pop(itemKey)
        upsertConfigurationItem("capabilities", listCapabilities)
        reportDeviceCapabilities()
    return


def getLanIpAddress():
    # Returns the current LAN IP address.
    data = runNativeCommand(["ip", "route", "list"])
    split_data = data.split()
    ipaddr = split_data[split_data.index("src")+1]
    return ipaddr




###########################   MQTT


def checkIsAgentMqttBrokerInstalled():
    return runNativeCommand(["systemctl", "is-active", "mosquitto"]).strip() == "active"


# Generate a password hash in the mosquitto_passwd format ($7$ = PBKDF2-SHA512)
# so the password never has to be passed to mosquitto_passwd on a command line
def mosquittoPasswordHash(password, iterations = 101):
    salt = secrets.token_bytes(12)
    digest = hashlib.pbkdf2_hmac('sha512', password.encode('utf8'), salt, iterations, 64)
    return "$7$" + str(iterations) + "$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def generatePassword():
    return secrets.token_urlsafe(32)



###########################   RUN OS COMMANDS


# Prefix a command with sudo only when the agent is not already root
def asRoot(commandParts):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return list(commandParts)
    return ["sudo"] + list(commandParts)


# Runs command on operating system (no shell). Returns stdout.
def runNativeCommand(arrayCommandLineParts = []):
    log("Running native command " + str(arrayCommandLineParts))
    try:
        s = subprocess.run(arrayCommandLineParts, capture_output=True, text=True, timeout=60).stdout
        log('Response after cmd:' + str(s))
        return s
    except Exception as e:
        log('Failed to run command:' + str(arrayCommandLineParts) + " : " + str(e))
        return 'unknown'


def runNativeCommandLine(strCommandLine):
    return runNativeCommand(strCommandLine.split())


# Execute a command (list of arguments, no shell) and return the exit code and stdout
# scriptResponse is a tuple of (exitStatusCode, stdout)
def runCommandWithTimeout(commandParts, timeout, cwd = None):
    timeout = int(timeout)
    if isinstance(commandParts, str):
        raise TypeError("runCommandWithTimeout takes a list of arguments, not a shell string")
    log("Running command with timeout " + str(commandParts) + " timeout:" + str(timeout))
    try:
        p = subprocess.run(commandParts, cwd=cwd, text=True, stdout=subprocess.PIPE, \
            stderr=subprocess.STDOUT, timeout=timeout)
        log("command finished. Exit code: " + str(p.returncode))
        return (p.returncode, p.stdout)
    except subprocess.TimeoutExpired as e:
        log("command timed out after " + str(timeout) + "s")
        output = e.stdout.decode('utf8', errors='replace') if isinstance(e.stdout, bytes) else e.stdout
        return (-1, output)
    except OSError as e:
        log("command failed to start: " + str(e))
        return (-1, str(e))


# Returns operating system detail
def getOperatingSystem():
    try:
        with open("/etc/os-release") as osRelease:
            for line in osRelease:
                if line.startswith("PRETTY_NAME="):
                    return line.split('=', 1)[1].strip().strip('"')
    except OSError:
        pass
    return runNativeCommand(["uname", "-a"])



def getRam():
    #Returns a tuple (total ram, available ram) in megabytes.
    try:
        s = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout
        lines = s.split("\n")
        return ( int(lines[1].split()[1]), int(lines[2].split()[3]) )
    except (OSError, IndexError, ValueError):
        return (0, 0)

def getProcessCount():
    #Returns the number of processes.
    try:
        s = subprocess.run(["ps","-e"], capture_output=True, text=True).stdout
        return len(s.split("\n"))
    except OSError:
        return 0

def getUptime():
    #Returns a tuple (uptime, 1 min load average).
    try:
        s = subprocess.run(["uptime"], capture_output=True, text=True).stdout
        load_split = s.split("load average: ")
        load_one = float(load_split[1].split(",")[0])
        up = load_split[0]
        up_pos = up.rfind(",", 0, len(up)-4)
        up = up[:up_pos].split("up ")[1]
        return (up, load_one)
    except (OSError, IndexError, ValueError):
        return ("", 0)

def getUptimeProcess():
    # Returns uptime in seconds
    try:
        with open("/proc/uptime") as uptimeFile:
            return int(float(uptimeFile.read().split(" ")[0]))
    except (OSError, ValueError):
        return 0


###########################   Utilities


def updateDeviceLabelOnServer(labelKey, labelValue):
    r = httpGet(conf['server'] + '/api/v1/devices/' + conf['UUID'])
    if (r.status_code == 200 and json.loads(r.content)['totalRecords'] == 1):
        dataToSend = json.loads(r.content)['records'][0]
        if (dataToSend['labels'] is None):
            dataToSend['labels'] = {}
        dataToSend['labels'][labelKey] = labelValue
        r = httpPut(conf['server'] + '/api/v1/devices/' + conf['UUID'], dataToSend)
        if (r.status_code == 200):
            return r
        else:
            log("Issue while updating device label on server: " + str(r.status_code))
            log(r.content)
            return r
    else:
        log("Issue while getting device label from server: " + str(r.status_code))
        log(r.content)
        return r


def getMilliSecondsSinceEpoch():
    return int(time.time() * 1000)


# Is a string valid json
def isJson(myjson):
    try:
        json.loads(myjson)
    except (ValueError, TypeError):
        return False
    return True


# For a file in the current dir, get the absolute location
def getAbsoluteFileLocation(inputFilename):
    return os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), inputFilename)


# Only directories within the installation directory may be wiped or created by the agent
def assertWithinInstallationDir(dirToCheck):
    realInstall = os.path.realpath(installationDir)
    realDir = os.path.realpath(dirToCheck)
    if os.path.commonpath([realInstall, realDir]) != realInstall or realDir == realInstall:
        raise ValueError("Refusing to manage directory outside " + installationDir + ": " + dirToCheck)


# Remove a directory if it already exists and make it again, ready for writing to (root only)
def provideFreshDirectory(dirToClean, mode = 0o700):
    assertWithinInstallationDir(dirToClean)
    shutil.rmtree(dirToClean, ignore_errors=True)
    ensureDirectoryExists(dirToClean, mode)
    return


# Ensure a directory exists for writing to (root only by default)
def ensureDirectoryExists(dir, mode = 0o700):
    assertWithinInstallationDir(dir)
    os.makedirs(dir, mode=mode, exist_ok=True)
    os.chmod(dir, mode)
    return


# Edit a json file to upsert a parameter
def upsertJsonEntry(jsonFileToEdit, jsonKey, jsonValue):
    fileContent = None
    with open(jsonFileToEdit) as data_file:
        fileContent = json.load(data_file)
    fileContent[jsonKey] = jsonValue
    writeJsonFile(jsonFileToEdit, fileContent)
    return


# Reduce a string to only safe characters
def safeChars(inputStr):
    return(''.join(ch for ch in inputStr if ch.isalnum()))


def generateUuid():
    return str(uuid.uuid4())
