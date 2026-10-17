# Davra Device Agent
# This runs on a device so it communicates with the Davra Server.
# It facilitates running Applications on Devices and running Jobs.
# It should run continuously (as a service) on a device.
#
#
import ast
import hashlib
import json
import os
import os.path
import shutil
import sys
import tarfile
import tempfile
import time

import paho.mqtt.client as mqtt
import requests

import davra_lib as comDavra
# If you add new libraries to the agent, update requirements.in and regenerate requirements.txt


# The job details of currently running job for this device.
# Files are used to track the currently running Job, script and function.
# Each get a dirctory to themselves and a json for tracking.
# The directory "currentJob" etc may be removed when job is finished.
currentJobDir = comDavra.installationDir + '/currentJob'
currentJobJson = currentJobDir + '/job.json'
currentFunctionDir = comDavra.installationDir + "/currentFunction"
currentFunctionJson = currentFunctionDir + "/currentFunction.json"
appsDir = comDavra.installationDir + '/apps'
# Some flags to cache in RAM whether a function/script/job is running
# rather than checking the hard drive continuously
flagIsFunctionRunning = False
flagIsScriptRunning = False
flagIsJobRunning = False

# Largest application package the agent will download
appMaxDownloadBytes = 512 * 1024 * 1024

# Config keys which cannot be changed remotely by the updateAgentConfig function
protectedConfigKeys = ("server", "apiToken", "UUID", "capabilities", "insecureDevMode", "service", "agentRepository")
protectedConfigKeyPrefixes = ("mqtt", "localMqtt")
# Numeric config keys which can be changed remotely, with their allowed range
numericConfigKeyRanges = { "heartbeatInterval": (10, 86400), "scriptMaxTime": (1, 86400) }

# Messages a device app may send to the agent over the local mqtt broker.
# Anything else (eg runFunctionOnAgent) is ignored: agent functions are only run when the server asks.
appMessageTypes = ("registerCapability", "connectToAgent", "retrieveConfigFromAgent", "finishedFunctionOnApp", "sendIotData")
# Capability names reserved for the agent's own functions
agentCapabilityPrefix = "agent-action-"


def sendHeartbeatMetricsToServer():
    heartbeatValue = {
        "davraAgentVersion": comDavra.davraAgentVersion,
        "heartbeatInterval": comDavra.conf['heartbeatInterval']
    }
    if comDavra.isInsecureDevMode():
        heartbeatValue["insecureDevMode"] = True
    dataToSend = [{
        "UUID": comDavra.conf['UUID'],
        "name": "uptime",
        "value": comDavra.getUptimeProcess(),
        "msg_type": "datum"
    }, {
        "UUID": comDavra.conf['UUID'],
        "name": "cpu",
        "value": comDavra.getUptime()[1],
        "msg_type": "datum",
    }, {
        "UUID": comDavra.conf['UUID'],
        "name": "ram",
        "value": comDavra.getRam()[1],
        "msg_type": "datum",
    }, {
        "UUID": comDavra.conf['UUID'],
        "name": "davra.agent.heartbeat",
        "value": heartbeatValue,
        "msg_type": "event"
    }]
    comDavra.logInfo('Sending heartbeat data to: ' + comDavra.conf['server'] + ": " + comDavra.conf['UUID'])
    statusCode = comDavra.sendDataToServer(dataToSend).status_code
    comDavra.log('Response after sending heartbeat data: ' + str(statusCode))
    return



###########################   JOBS

def checkForPendingJob():
    dataToSend = {
        "deviceUUID": comDavra.conf['UUID'],
        "deviceStatus": "pending",
        "jobStatus": "active",
        "oldest": True
    }
    r = comDavra.httpPut(comDavra.conf['server'] + '/api/v1/jobs', dataToSend)
    if (r.status_code == 200):
        pendingJobs = json.loads(r.content) if comDavra.isJson(r.content) else []
        if(isinstance(pendingJobs, list) and len(pendingJobs) > 0):
            comDavra.log('Pending job to run: ' + str(pendingJobs[0]))
            runDavraJob(pendingJobs[0])
        else:
            comDavra.log('No pending job to run.')
        return
    else:
        comDavra.logError("Issue while checking for pending job. " + str(r.status_code))
        comDavra.logError(r.content)
        return(r.status_code)


# For any type of job, determine which type (eg script) and run it
def runDavraJob(jobObject):
    global flagIsJobRunning
    # Catch situation where a job is already running
    if(os.path.isfile(currentJobJson) == True):
        comDavra.logWarning('A job is already running. Will not start another job. ' + str(jobObject.get("UUID")))
        return
    comDavra.log('Start Run of job ' + str(jobObject.get("UUID")))
    try:
        flagIsJobRunning = True
        # Write this job to disk as the current job
        jobObject['devices'][0]['startTime'] = comDavra.getMilliSecondsSinceEpoch()
        jobObject['devices'][0]['status'] = 'running'
        comDavra.provideFreshDirectory(currentJobDir)
        comDavra.writeJsonFile(currentJobJson, jobObject)
        if ('jobConfig' in jobObject and jobObject['jobConfig']['type'].lower() == 'runfunction'):
            comDavra.logInfo('Job Run: type is runFunction. ' + jobObject['jobConfig']['functionName'])
            runFunction(jobObject['jobConfig']['functionName'], jobObject['jobConfig'].get('functionParameterValues') or {})
            return
        # Reaching here means the job type was not recognised so that is a failed situation
        updateJobWithResult('failed', 'Unknown job type')
        checkCurrentJob()
        return
    except Exception as e:
        comDavra.logError('Job Error ' + str(e))
        updateJobWithResult('failed', 'Job error: ' + str(e))
        checkCurrentJob()
        return



# Check if the current job is finished
def checkCurrentJob():
    if(os.path.isfile(currentJobJson) == True):
        with open(currentJobJson) as data_file:
            jobObject = json.load(data_file)
        if(jobObject['devices'][0]['status'] == 'completed' or jobObject['devices'][0]['status'] == 'failed'):
            reportJobStatus()
    return


def updateJobWithResult(status, responseText):
    # Update the currentJob file on disk with the result. That will be noticed upon next "checkCurrentJob".
    jobObject = None
    if(os.path.isfile(currentJobJson) == True):
        with open(currentJobJson) as data_file:
            jobObject = json.load(data_file)
        jobObject['devices'][0]['endTime'] = comDavra.getMilliSecondsSinceEpoch()
        jobObject['devices'][0]['status'] = status
        jobObject['devices'][0]['response'] = responseText
        comDavra.writeJsonFile(currentJobJson, jobObject)
    # Finished writing the file to disk indicating job progress/completion
    return


# When a job has finished, send the result to the Davra server
def reportJobStatus():
    global flagIsJobRunning
    comDavra.log('Current job is finished so reporting it to server now')
    jobObject = None
    with open(currentJobJson) as data_file:
        jobObject = json.load(data_file)
    deviceJobObject = jobObject['devices'][0]
    apiEndPoint = comDavra.conf['server'] + '/api/v1/jobs/' + jobObject['UUID'] + '/' + deviceJobObject['UUID']
    comDavra.logInfo('Reporting job update to server: ' + apiEndPoint + ' : ' + json.dumps(deviceJobObject))
    r = comDavra.httpPut(apiEndPoint, deviceJobObject)
    if (r.status_code == 200):
        comDavra.log("Updated server after running job.")
        # The job has completed and been reflected at the server so delete the currentJobJson file
        os.remove(currentJobJson)
    else:
        comDavra.log("Issue while updating server after running job. " + str(r.status_code))
        comDavra.log(r.content)
    # Report job event to server as an iotdata event
    eventToSend = {
        "UUID": deviceJobObject['UUID'],
        "name": "davra.job.finished",
        "msg_type": "event",
        "value": deviceJobObject,
        "tags": {
            "status": deviceJobObject["status"]
        }
    }
    apiEndPoint = comDavra.conf['server'] + '/api/v1/iotdata'
    r = comDavra.httpPut(apiEndPoint, eventToSend)
    if (r.status_code == 200):
        comDavra.log("Sent event to server after running job.")
    else:
        comDavra.logError("Issue while sending event to server after running job. " + str(r.status_code))
        comDavra.logError(r.content)
    comDavra.provideFreshDirectory(currentJobDir) # Remove the file and dir on disk
    flagIsJobRunning = False
    return



# Report an event to the Davra server indicating the agent started
def reportAgentStarted():
    eventToSend = {
        "UUID": comDavra.conf['UUID'],
        "name": "davra.agent.started",
        "msg_type": "event",
        "value": comDavra.redactedConfig()
    }
    r = comDavra.sendDataToServer(eventToSend)
    if (r.status_code == 200):
        comDavra.logInfo("Sent event to server to indicate agent started")
    # Update the device labels to reflect this agent version
    comDavra.logInfo("Running davraAgentVersion:" + comDavra.davraAgentVersion)
    comDavra.updateDeviceLabelOnServer("davraAgentVersion", comDavra.davraAgentVersion)
    return




###########################   RUN FUNCTIONS

# Run a function which the agent knows what to do, or get the appropriate app to run it
# This function just kicks it off. Another function will check in later to see if it is finished
def runFunction(functionName, functionParameterValues):
    global flagIsFunctionRunning
    if not isinstance(functionParameterValues, dict):
        functionParameterValues = {}
    # Always assign a uuid to a function if not already
    if(("functionUuid" in functionParameterValues) == False):
        functionParameterValues["functionUuid"] = comDavra.generateUuid()
    # Put a file to indicate what is happening and start the function
    comDavra.provideFreshDirectory(currentFunctionDir)
    functionInfo = { 'functionName': functionName, \
        'functionParameterValues': functionParameterValues, \
        'status': 'running', \
        'startTime': comDavra.getMilliSecondsSinceEpoch() }
    comDavra.writeJsonFile(currentFunctionJson, functionInfo)
    # Only run functions which are within capabilities
    if((functionName in comDavra.conf.get("capabilities", {})) is False):
        comDavra.logError('Error: Attemping to to run a function which is not in capabilities: ' + str(functionName))
        comDavra.logError('Error: Capabilities: ' + str(comDavra.conf.get("capabilities")))
        comDavra.upsertJsonEntry(currentFunctionJson, 'status', 'failed')
        return
    #
    comDavra.log('Running a function:' + json.dumps(functionInfo))
    flagIsFunctionRunning = True
    #
    # Is this capability something the agent knows how to do
    if(functionName in agentCapabilityFunctions):
        comDavra.log('Will run this function within the agent')
        agentCapabilityFunctions[functionName](functionParameterValues)
    else:
        # Send it onwards to the apps who may be able to do it
        comDavra.log('Will run this function within an app rather than the agent')
        sendMessageFromAgentToApps(functionInfo)
    return


# Check if a function is finished running and if it was part of a job
def checkFunctionFinished():
    global flagIsFunctionRunning
    currentFunctionInfo = None
    if(os.path.isfile(currentFunctionJson) == True):
        with open(currentFunctionJson) as data_file:
            currentFunctionInfo = json.load(data_file)
        if("status" in currentFunctionInfo):
            if(currentFunctionInfo["status"] == 'completed' or currentFunctionInfo["status"] == 'failed'):
                # Function has finished, report back if part of a currently running job
                functionResponse = currentFunctionInfo["response"] if "response" in currentFunctionInfo else ""
                reportFunctionFinishedAsEventToServer(currentFunctionInfo)
                updateJobWithResult(currentFunctionInfo["status"], functionResponse)
                comDavra.provideFreshDirectory(currentFunctionDir) # Wipe the currently running function files
                flagIsFunctionRunning = False
                comDavra.log('Function finished ' + json.dumps(currentFunctionInfo))
            else:
                # Has the function been running for too long. If so, declare it as failed
                if(("startTime" in currentFunctionInfo) is True):
                    if(comDavra.getMilliSecondsSinceEpoch() - int(currentFunctionInfo["startTime"]) \
                    > int(comDavra.conf["scriptMaxTime"]) * 1000):
                        comDavra.logWarning('Function has been running for too long - declare it failed')
                        currentFunctionInfo["status"] = "failed"
                        currentFunctionInfo["endTime"] = comDavra.getMilliSecondsSinceEpoch()
                        comDavra.writeJsonFile(currentFunctionJson, currentFunctionInfo)
                        reportFunctionFinishedAsEventToServer(currentFunctionInfo)
                        flagIsFunctionRunning = False
                        comDavra.logWarning('Function finished due to timeout ' + json.dumps(currentFunctionInfo))
            # If a function has finished, it may have been part of a job. Check if that is finished
            checkCurrentJob()
            return
        else:
            comDavra.logError('Error situation. Function has no status. Should not occur.')
    return


# Report function-finished event to server as an iotdata event
def reportFunctionFinishedAsEventToServer(currentFunctionInfo):
    if(("functionParameterValues" in currentFunctionInfo) == False \
    or ("functionUuid" in currentFunctionInfo["functionParameterValues"]) == False):
        return
    eventToSend = {
        "UUID": comDavra.conf['UUID'],
        "name": "davra.function.finished",
        "msg_type": "event",
        "value": currentFunctionInfo,
        "tags": {
            "functionUuid": currentFunctionInfo["functionParameterValues"]["functionUuid"]
        }
    }
    r = comDavra.sendDataToServer(eventToSend)
    comDavra.log("Sent event to server to indicate function finished. Response " + str(r.status_code))


# When a function was enacted by a device app rather than the agent, it reports back the
# finished function information via mqtt. This receives that message and updates the
# agent's understanding of the function's progress.
# The report is only accepted for the function which is currently running on an app.
def updateFunctionStatusAsReportedByDeviceApp(functionInfo):
    comDavra.log('App reported it finished a function ' + str(functionInfo))
    if not os.path.isfile(currentFunctionJson):
        comDavra.logWarning('App reported a finished function but no function is running. Ignored.')
        return
    with open(currentFunctionJson) as data_file:
        currentFunctionInfo = json.load(data_file)
    functionName = functionInfo.get("finishedFunctionOnApp")
    if currentFunctionInfo.get("status") != "running" or functionName != currentFunctionInfo.get("functionName"):
        comDavra.logWarning('App reported a function which is not the running function. Ignored: ' + str(functionName))
        return
    if functionName in agentCapabilityFunctions:
        comDavra.logWarning('App reported an agent function as finished. Ignored: ' + str(functionName))
        return
    reportedParams = functionInfo.get("functionParameterValues")
    reportedUuid = functionInfo.get("functionUuid") \
        or (reportedParams.get("functionUuid") if isinstance(reportedParams, dict) else None)
    if reportedUuid is not None and reportedUuid != currentFunctionInfo["functionParameterValues"].get("functionUuid"):
        comDavra.logWarning('App reported a function with the wrong functionUuid. Ignored.')
        return
    if functionInfo.get("status") not in ('completed', 'failed'):
        comDavra.logWarning('App reported an invalid function status. Ignored: ' + str(functionInfo.get("status")))
        return
    comDavra.upsertJsonEntry(currentFunctionJson, 'response', functionInfo.get("response", ""))
    comDavra.upsertJsonEntry(currentFunctionJson, 'status', functionInfo["status"])
    checkFunctionFinished()
    return

# Function: Reboot this device
def agentFunctionReboot(functionParameterValues):
    # Put a file to indicate what is happening and start the reboot process
    comDavra.writeJsonFile(currentFunctionDir + '/doingReboot.json', { 'doingRebootAsPartOfFunction': True })
    comDavra.logInfo('Function: Reboot Device, starting')
    comDavra.runCommandWithTimeout(comDavra.asRoot(['reboot']), comDavra.conf["scriptMaxTime"])


# Check if we are just back after a purposeful reboot as part of a job or function
def checkIfJustBackAfterRebootTask():
    currentFunctionInfo = None
    if(os.path.isfile(currentFunctionJson) == True):
        with open(currentFunctionJson) as data_file:
            currentFunctionInfo = json.load(data_file)
        if(currentFunctionInfo["functionName"] == 'agent-action-rebootDevice'):
            comDavra.log('checkIfJustBackAfterRebootTask: True. Function completed')
            comDavra.upsertJsonEntry(currentFunctionJson, 'response', str(comDavra.getUptime()))
            comDavra.upsertJsonEntry(currentFunctionJson, 'status', 'completed')
            checkFunctionFinished()


# Download an application package to a file, without credentials, checking size and optional sha256.
# Returns the sha256 hex digest of what was downloaded.
def downloadAppPackage(url, destinationFile, expectedSha256 = ""):
    comDavra.validateServerUrl(url)
    sha256 = hashlib.sha256()
    downloadedBytes = 0
    with requests.get(url, stream=True, timeout=comDavra.httpTimeout) as r:
        r.raise_for_status()
        fd = os.open(destinationFile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as outfile:
            for chunk in r.iter_content(chunk_size=65536):
                downloadedBytes += len(chunk)
                if downloadedBytes > appMaxDownloadBytes:
                    raise ValueError('Application package is larger than ' + str(appMaxDownloadBytes) + ' bytes')
                sha256.update(chunk)
                outfile.write(chunk)
    digest = sha256.hexdigest()
    if expectedSha256:
        if digest != expectedSha256:
            raise ValueError('Application package SHA-256 mismatch. Expected ' + expectedSha256 + ' got ' + digest)
        comDavra.logInfo('Application package SHA-256 verified')
    else:
        comDavra.logWarning('No SHA-256 supplied for application package; integrity not verified. SHA-256 is ' + digest)
    return digest


# Function: Push an Application which has an install.sh onto this device to run as a service
# functionParameterValues should have "Installation File" which should be a tar.gz containing the service file,
# an install.sh. "SHA-256" is optional and, when supplied, must match the downloaded file.
def agentFunctionPushAppWithInstaller(functionParameterValues):
    comDavra.logInfo('Function: Pushing Application onto device to run as a service ' + str(functionParameterValues))
    installationFile = functionParameterValues.get("Installation File")
    if not installationFile:
        comDavra.logWarning('Action parameters missing, nothing to do')
        comDavra.upsertJsonEntry(currentFunctionJson, 'response', 'Installation File missing')
        comDavra.upsertJsonEntry(currentFunctionJson, 'status', 'failed')
        checkFunctionFinished()
        return
    expectedSha256 = str(functionParameterValues.get("SHA-256") or "").strip().lower()
    tmpPath = tempfile.mkdtemp(prefix="davra-app-") # Private (0700) directory
    try:
        packageFile = os.path.join(tmpPath, "package")
        downloadAppPackage(installationFile, packageFile, expectedSha256)
        comDavra.ensureDirectoryExists(appsDir, 0o755)
        installedAppPath = appsDir + '/' + str(comDavra.getMilliSecondsSinceEpoch())
        comDavra.ensureDirectoryExists(installedAppPath, 0o755)
        # The "data" filter refuses absolute paths, ".." and links outside the destination
        with tarfile.open(packageFile, "r:*") as tar:
            # nosemgrep: tarfile-extractall-traversal -- the "data" filter blocks traversal (see tests)
            tar.extractall(installedAppPath, filter="data")
        installScript = os.path.join(installedAppPath, "install.sh")
        if not os.path.isfile(installScript):
            raise ValueError('Application package has no install.sh')
        # Ensure the install.sh is unix format
        with open(installScript, 'rb') as scriptFile:
            scriptContent = scriptFile.read()
        with open(installScript, 'wb') as scriptFile:
            scriptFile.write(scriptContent.replace(b'\r\n', b'\n'))
        installResponse = comDavra.runCommandWithTimeout(['bash', './install.sh'], \
            comDavra.conf["scriptMaxTime"], cwd=installedAppPath)
        comDavra.log('Installation response: ' + str(installResponse[1]))
        scriptStatus = 'completed'  if (installResponse[0] == 0) else 'failed'
        comDavra.upsertJsonEntry(currentFunctionJson, 'response', str(installResponse[1]))
        comDavra.upsertJsonEntry(currentFunctionJson, 'status', scriptStatus)
    except Exception as e:
        comDavra.logError('Failed to install application: ' + str(installationFile) + " : Error: " + str(e))
        comDavra.upsertJsonEntry(currentFunctionJson, 'response', 'Failed to install application: ' + str(e))
        comDavra.upsertJsonEntry(currentFunctionJson, 'status', 'failed')
    finally:
        shutil.rmtree(tmpPath, ignore_errors=True)
    comDavra.log('Finished agentFunctionPushAppWithInstaller')
    checkFunctionFinished()
    return


# Function: Report the device configuration to the server
def agentFunctionReportAgentConfig(functionParameterValues):
    comDavra.logInfo('Function: Reporting the agent config to server')
    comDavra.reportDeviceConfigurationToServer()
    comDavra.upsertJsonEntry(currentFunctionJson, 'response', comDavra.redactedConfig())
    comDavra.upsertJsonEntry(currentFunctionJson, 'status', 'completed')
    checkFunctionFinished()
    return


# Is a config key one which may be changed remotely
def isConfigKeyUpdatable(key):
    if not isinstance(key, str) or not key:
        return False
    if key in protectedConfigKeys or key.startswith(protectedConfigKeyPrefixes):
        return False
    return True


# Function: Update a configuration item on the device. Connection and security settings cannot be changed.
def agentFunctionUpdateAgentConfig(functionParameterValues):
    comDavra.logInfo('Function: Updating the agent config ' + str(functionParameterValues))
    key = functionParameterValues.get("key")
    value = functionParameterValues.get("value")
    try:
        if not isConfigKeyUpdatable(key):
            raise ValueError('Config key cannot be changed remotely: ' + str(key))
        if key in numericConfigKeyRanges:
            value = int(value)
            (minValue, maxValue) = numericConfigKeyRanges[key]
            if value < minValue or value > maxValue:
                raise ValueError(key + ' must be between ' + str(minValue) + ' and ' + str(maxValue))
    except ValueError as e:
        comDavra.logError('updateAgentConfig refused: ' + str(e))
        comDavra.upsertJsonEntry(currentFunctionJson, 'response', str(e))
        comDavra.upsertJsonEntry(currentFunctionJson, 'status', 'failed')
        checkFunctionFinished()
        return
    comDavra.upsertConfigurationItem(key, value)
    comDavra.reportDeviceConfigurationToServer()
    comDavra.upsertJsonEntry(currentFunctionJson, 'response', comDavra.redactedConfig())
    comDavra.upsertJsonEntry(currentFunctionJson, 'status', 'completed')
    checkFunctionFinished()
    return


# Function: Run a bash script
# Take a set of lines and write them to a shell script then execute that script
def agentFunctionRunScriptBash(functionParameterValues):
    if "script" not in functionParameterValues:
        comDavra.upsertJsonEntry(currentFunctionJson, 'response', 'script missing')
        comDavra.upsertJsonEntry(currentFunctionJson, 'status', 'failed')
        comDavra.logError('Could not run script as function because no script to run')
        checkFunctionFinished()
        return
    # Put the script into the (root only) function dir. O_EXCL refuses a file which is already there.
    scriptPath = currentFunctionDir + "/script.sh"
    fd = os.open(scriptPath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd, 'w') as scriptFile:
        scriptFile.write(str(functionParameterValues["script"]))
    comDavra.logInfo('Running script ' + str(functionParameterValues["script"]))
    # Run the script with -x flag so it prints each command before ruuning it.
    # This allows the UI to show it formatted better for user on jobs page
    scriptResponse = comDavra.runCommandWithTimeout(comDavra.asRoot(['bash', '-x', scriptPath]), \
        comDavra.conf["scriptMaxTime"], cwd=currentFunctionDir)
    # scriptResponse is a tuple of (exitStatusCode, stdout) . For exitStatusCode: 0 = success, 1 = failed )
    comDavra.log("Script response: " + str(scriptResponse[1]))
    scriptStatus = 'completed'  if (scriptResponse[0] == 0) else 'failed'
    comDavra.upsertJsonEntry(currentFunctionJson, 'response', str(scriptResponse[1]))
    comDavra.upsertJsonEntry(currentFunctionJson, 'status', scriptStatus)
    checkFunctionFinished()



###########################   REPORT KNOWN AGENT CAPABILITIES


# Set which actions this agent can do and inform the server of these capabilities
agentCapabilityFunctions = {} # Keep a pointer to the functions which enact each of capabilities
def registerAgentCapabilities(functionName, functionDetails, functionToCallToEnactCapability):
    global agentCapabilityFunctions
    agentCapabilityFunctions[functionName] = functionToCallToEnactCapability
    # Inform the server that this device has these capabilities
    comDavra.registerDeviceCapability(functionName, functionDetails)


# Register the functions defined above as those which the agent can do
# This will then be sent to the server informing the server of agent capabilities
# This makes its way into the value at /api/v1/devices/<deviceUuid> in "capabilities"
# Device Applications may also register their own capabilities separately
#
def registerAllAgentCapabilities():
    registerAgentCapabilities('agent-action-pushAppWithInstaller', { \
        "functionParameters": { "Installation File": "file", "SHA-256": "string" }, \
        "functionLabel": "Push Device App (with installer)", \
        "functionDescription": "To run a device Application alongside the Device Agent on a device. Supply a tar.gz file containing an install.sh script to install it, and optionally its SHA-256 checksum." \
    }, agentFunctionPushAppWithInstaller)
    registerAgentCapabilities('agent-action-rebootDevice', { \
        "functionParameters": {}, \
        "functionLabel": "Reboot Device", \
        "functionDescription": "Reboot the device immediately" \
    }, agentFunctionReboot)
    registerAgentCapabilities('agent-action-reportAgentConfig', { \
        "functionParameters": {}, \
        "functionLabel": "Report Agent Config to Server", \
        "functionDescription": "Send the agent configuration from agent to server" \
    }, agentFunctionReportAgentConfig)
    registerAgentCapabilities('agent-action-updateAgentConfig', { \
        "functionParameters": { "key": "string", "value": "string" }, \
        "functionLabel": "Update Config on Device Agent", \
        "functionDescription": "Upsert a configuration item on the device" \
    }, agentFunctionUpdateAgentConfig)
    registerAgentCapabilities('agent-action-runScriptBash', { \
        "functionParameters": { "script": "textarea" }, \
        "functionLabel": "Run bash script on Device", \
        "functionDescription": "Run a bash script once on the device, launched by the Device Agent" \
    }, agentFunctionRunScriptBash)


###########################   MQTT helpers

# Create an mqtt client which works with paho-mqtt 1.x and 2.x
def newMqttClient():
    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    return mqtt.Client()


# Parse an mqtt payload into a dict.
# Strict json first, then a python-style dict literal (eg single quoted) without altering its content.
# Returns None if the payload is not a json/dict object.
def parseMqttPayload(payloadBytes):
    payload = payloadBytes.decode('utf8', errors='replace') if isinstance(payloadBytes, bytes) else str(payloadBytes)
    try:
        parsed = json.loads(payload)
    except ValueError:
        try:
            parsed = ast.literal_eval(payload)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            return None
    return parsed if isinstance(parsed, dict) else None


###########################   MQTT Broker running on device

# The callback for when the client receives a CONNACK response from the broker on the device.
def mqttOnConnectDevice(client, userdata, flags, resultCode, properties = None):
    comDavra.log('Mqtt Device Broker: Connected with result code ' + str(resultCode))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("/agent")
    return


# The callback for when a message is received from the mqtt broker on the device.
def mqttOnMessageDevice(client, userdata, msg):
    parsed = parseMqttPayload(msg.payload)
    if(parsed is not None):
        processMessageFromAppToAgent(parsed)
    else:
        comDavra.logError('ERROR: Mqtt Device Broker: Received NON json Mqtt message: ' + str(msg.payload[:200]))
    return


# Setup the MQTT client talking to the broker on the device
clientOfDevice = None
def connectToDeviceBroker():
    global clientOfDevice
    if("mqttBrokerAgentHost" in comDavra.conf and len(comDavra.conf["mqttBrokerAgentHost"]) > 3):
        clientOfDevice = newMqttClient()
        clientOfDevice.on_connect = mqttOnConnectDevice
        clientOfDevice.on_message = mqttOnMessageDevice
        # The agent has its own credentials on the device broker. The platform api token is never used locally.
        if comDavra.conf.get("localMqttAgentPassword"):
            clientOfDevice.username_pw_set(username = comDavra.localMqttAgentUsername, \
                password = comDavra.conf["localMqttAgentPassword"])
        else:
            comDavra.logWarning('No credentials for the device mqtt broker. Re-run davra_setup.py to secure it.')
        comDavra.logInfo('Starting to connect to MQTT broker running on device ' + comDavra.conf["mqttBrokerAgentHost"])
        try:
            clientOfDevice.connect(comDavra.conf["mqttBrokerAgentHost"])
            clientOfDevice.loop_start() # Starts another thread to monitor incoming messages
        except Exception as e:
            comDavra.logError('Experienced error connecting to mqtt at ' + comDavra.conf["mqttBrokerAgentHost"] + ":" + str(e))
    else:
        comDavra.logError('No MQTT broker configured on device')




###########################   Process Messages from Device Application to Device Agent

# These messages arrive by mqtt from app to agent. Any local process can publish them,
# so they are untrusted: only the message types in appMessageTypes are acted on.
# msg should be a json object
def processMessageFromAppToAgent(msg):
    # Ignore any messages this agent published
    if("fromAgent" in msg):
        return
    comDavra.log('processMessageFromAppToAgent: incoming msg: ' + str(msg))
    if("runFunctionOnAgent" in msg):
        comDavra.logWarning('Ignoring runFunctionOnAgent from the device mqtt broker. ' \
            + 'Agent functions can only be run by the Davra server: ' + str(msg["runFunctionOnAgent"]))
    if("registerCapability" in msg):
        capabilityName = msg["registerCapability"]
        capabilityDetails = msg["capabilityDetails"] if "capabilityDetails" in msg else {}
        if not isinstance(capabilityName, str) or not capabilityName or len(capabilityName) > 100 \
        or capabilityName.startswith(agentCapabilityPrefix) or not isinstance(capabilityDetails, dict):
            comDavra.logWarning('Refusing app capability registration: ' + str(capabilityName))
        else:
            comDavra.registerDeviceCapability(capabilityName, capabilityDetails)
    if("connectToAgent" in msg):
        applicationName = msg["connectToAgent"]
        comDavra.log('From app to agent, app announcing it is running: ' + str(applicationName))
        time.sleep(0.1)
        sendHeartbeatToDeviceApps()
    if("retrieveConfigFromAgent" in msg):
        comDavra.log('From app to agent, app requesting config')
        sendMessageFromAgentToApps({"agentConfig": comDavra.redactedConfig()})
    if("finishedFunctionOnApp" in msg):
        functionName = msg["finishedFunctionOnApp"]
        comDavra.log('From app to agent, app announcing it finished running a function: ' + str(functionName))
        updateFunctionStatusAsReportedByDeviceApp(msg)
    if("sendIotData" in msg):
        comDavra.log('From app to agent, app announcing it has iotData to send ' + str(msg))
        sendIotDataToServer(msg)


# Send a message onto the mqtt topic which the Device Apps are lstening to
# Usually topic /agent on localhost
# msg should be valid json
def sendMessageFromAgentToApps(msg):
    if clientOfDevice is None:
        comDavra.log('sendMessageFromAgentToApps: no device mqtt broker, not sending: ' + str(msg))
        return
    comDavra.log('sendMessageFromAgentToApps: sending msg: ' + str(msg))
    msg['fromAgent'] = comDavra.davraAgentVersion
    clientOfDevice.publish('/agent', json.dumps(msg))


# Send a regular message to apps to inform them the agent is still available
def sendHeartbeatToDeviceApps():
    sendMessageFromAgentToApps({"agentHeartbeat": comDavra.getMilliSecondsSinceEpoch()})


# Send metrics and events to the platform server
def sendIotDataToServer(msgFromMqtt):
    comDavra.log('Sending iotdata to server ')
    dataFromAgent = msgFromMqtt["sendIotData"]
    if isinstance(dataFromAgent, str):
        try:
            dataFromAgent = json.loads(dataFromAgent)
        except ValueError:
            comDavra.logError('Not sending data to server as it is not json: ' + dataFromAgent[:200])
            return
    if isinstance(dataFromAgent, dict):
        dataFromAgent = [dataFromAgent]
    if not isinstance(dataFromAgent, list):
        comDavra.logError('Not sending data to server as it is not a list or object')
        return
    dataForServer = []
    for metric in dataFromAgent:
        if not isinstance(metric, dict):
            comDavra.logError('Not sending data to server as it is not an object: ' + str(metric))
            continue
        if (("UUID" in metric) == False):
            metric["UUID"] = comDavra.conf["UUID"]
        if ("timestamp" not in metric):
            metric["timestamp"] = comDavra.getMilliSecondsSinceEpoch()
        if ("name" in metric and "value" in metric and "msg_type" in metric):
            dataForServer.append(metric)
        else:
            comDavra.logError('Not sending data to server as it appears incomplete: ' + str(metric))
    if dataForServer:
        comDavra.log('Sending data to Server: ' + str(dataForServer))
        statusCode = comDavra.sendDataToServer(dataForServer).status_code
        comDavra.log('Response after sending iotdata to server: ' + str(statusCode))






###########################   MQTT Broker running on the Davra server (probably mqtt.davra.com)

# A device can subscribe to a topic for itself on "devices/<deviceUuid>"
# the Davra server will communicate over this topic to speak to the agent and vice-versa
# Usually the agent makes regular API calls to /api/v1/jobs to determine if there is a job to run and
# download the job. When a new job is created on the server, a "new-job-available" mesasge will be
# sent over mqtt to indicate this

# The callback for when the client (this agent) receives a CONNACK response from the broker
def mqttOnConnectServer(client, userdata, flags, resultCode, properties = None):
    comDavra.log("Mqtt Davra Server Broker: Connected with result code " + str(resultCode))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("devices/" + comDavra.conf["UUID"])
    return


# The callback for when a message is received from the broker on platform server.
def mqttOnMessageServer(client, userdata, msg):
    payload = msg.payload.decode('utf8', errors='replace')
    comDavra.log('Mqtt Davra Server Broker: Received Mqtt message: ' + payload)
    jsonPayload = parseMqttPayload(msg.payload)
    if jsonPayload is None:
        jsonPayload = { "stringMsg": payload }
    processMessageFromServerToAgent(jsonPayload)
    return


# Setup the MQTT client talking to the broker on the Davra server.
# TLS (with certificate and hostname verification, TLS 1.2+) is required unless in insecure dev mode.
clientOfServer = None
def mqttConnectToServer():
    global clientOfServer
    if not (("mqttBrokerServerHost" in comDavra.conf and len(comDavra.conf["mqttBrokerServerHost"]) > 3) \
    and "apiToken" in comDavra.conf):
        comDavra.logError('No MQTT broker configured for Davra server')
        return
    useTls = comDavra.conf.get("mqttBrokerServerUseTLS", True)
    if not useTls and not comDavra.isInsecureDevMode():
        comDavra.logError('Refusing to connect to the Davra MQTT broker without TLS. ' \
            + 'Re-run davra_setup.py, or use --insecure-dev for development only.')
        return
    for legacyKey in ("mqttBrokerServerTlsVersion", "mqttBrokerServerCertRequired", "mqttBrokerServerVerifyHostname"):
        if legacyKey in comDavra.conf:
            comDavra.logWarning('Config ' + legacyKey + ' is no longer supported and is ignored: ' \
                + 'TLS 1.2+ with certificate and hostname verification is always used.')
    mqttPort = int(comDavra.conf.get("mqttBrokerServerPort", 8883 if useTls else 1883))
    clientOfServer = newMqttClient()
    clientOfServer.on_connect = mqttOnConnectServer
    clientOfServer.on_message = mqttOnMessageServer
    clientOfServer.username_pw_set(username = comDavra.conf["UUID"], password = comDavra.conf["apiToken"])
    if useTls:
        try:
            clientOfServer.tls_set_context(comDavra.buildTlsContext( \
                comDavra.conf.get("mqttBrokerServerCaCert"), \
                comDavra.conf.get("mqttBrokerServerClientCert"), \
                comDavra.conf.get("mqttBrokerServerClientKey")))
        except Exception as e:
            comDavra.logError('Error configuring MQTT TLS: ' + str(e))
            return
    else:
        comDavra.logWarning('INSECURE DEV MODE: connecting to the Davra MQTT broker without TLS')
    try:
        comDavra.log('Connecting to MQTT broker at ' + comDavra.conf["mqttBrokerServerHost"] + ':' + str(mqttPort) + ' (TLS: ' + str(useTls) + ')')
        clientOfServer.connect(comDavra.conf["mqttBrokerServerHost"], port=mqttPort)
        clientOfServer.loop_start() # Starts another thread to monitor incoming messages
    except Exception as e:
        comDavra.logError('Experienced error connecting to mqtt at ' + comDavra.conf["mqttBrokerServerHost"] + ':' + str(mqttPort) + ' - ' + str(e))



###########################   Process Messages from Davra Server to this Device Agent

# These messages may arrive by mqtt from server to agent
# msg should be a json object
def processMessageFromServerToAgent(msg):
    comDavra.log('processMessageFromServerToAgent: incoming msg: ' + str(msg))
    if("stringMsg" in msg and msg["stringMsg"] == "davra.announcement:check-for-jobs"):
        comDavra.log('From server to device, new jobs might be available')
        checkForPendingJob()
    if("davra-announcement" in msg and msg["davra-announcement"] == "check-for-jobs"):
        comDavra.log('From server to device, new jobs might be available')
        checkForPendingJob()
    if("davra-function" in msg):
        comDavra.log('From server to device, run a function: ' + str(msg["davra-function"]))
        funcParamsToRun = {}
        if("functionParameterValues" in msg):
            funcParamsToRun = msg["functionParameterValues"]
        runFunction(msg["davra-function"], funcParamsToRun)




###########################   MAIN LOOP

def main():
    global flagIsFunctionRunning, flagIsJobRunning
    # Confirm configuration available from within the config file
    if('server' not in comDavra.conf or 'UUID' not in comDavra.conf):
        print("Configuration incomplete. Please run davra_setup.py first.")
        sys.exit(1)
    comDavra.logInfo('Starting Davra Device Agent.')
    comDavra.logInfo('Server: ' + comDavra.conf['server'] + ". Device: " + comDavra.conf['UUID'])
    if sys.version_info < comDavra.minimumPythonVersion:
        comDavra.logWarning('Python ' + '.'.join(map(str, comDavra.minimumPythonVersion)) + ' or later is required. Running ' + sys.version)
    try:
        comDavra.validateServerUrl(comDavra.conf['server'])
    except ValueError as e:
        comDavra.logError(str(e) + '. Re-run davra_setup.py, or use --insecure-dev for development only.')
        sys.exit(1)
    if comDavra.isInsecureDevMode():
        comDavra.logWarning('INSECURE DEV MODE IS ENABLED. Do not use this configuration in production.')
    connectToDeviceBroker()
    mqttConnectToServer()
    reportAgentStarted()
    # Run the reboot-finished check any time the program is started
    checkIfJustBackAfterRebootTask()
    registerAllAgentCapabilities()
    # Main loop to run forever.
    # Send heartbeat signal to server ocassionally, check for jobs and run them
    countMainLoop = 0
    while True:
        try:
            # Only every n seconds
            if(countMainLoop % int(comDavra.conf['heartbeatInterval']) == 0):
                # Emit a heartbeat for any apps listening on mqtt
                sendHeartbeatToDeviceApps()
                # Send a heartbeat to platform server
                sendHeartbeatMetricsToServer()
                # Check for any pending jobs
                checkForPendingJob()
            # check if a currently running job or function is finished
            # Only check if the flag indicates one is running but also every n iterations just in case
            if(flagIsFunctionRunning is True or countMainLoop % 60 == 0):
                checkFunctionFinished()
            if(flagIsJobRunning is True or countMainLoop % 60 == 0):
                checkCurrentJob()
            # Only occasionally, report all capabilities up to server just in case.
            if(countMainLoop % (int(comDavra.conf['heartbeatInterval']) * 10) == 0):
                comDavra.reportDeviceCapabilities()
        except KeyboardInterrupt:
            comDavra.logInfo("Davra Device Agent stopping")
            break
        except Exception as e:
            # Keep the agent running if one iteration fails (eg a malformed job from the server)
            comDavra.logError("Error in main loop: " + type(e).__name__ + ": " + str(e))
        countMainLoop += 1
        time.sleep(1)
# End Main loop


if __name__ == "__main__":
    main()
