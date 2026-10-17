#!/bin/bash
# Install or upgrade the Davra Agent. Run as root from the unpacked installation bundle.
# Arguments are passed on to davra_setup.py (eg --server https://myName.davra.com --token-file /path/to/token)
set -eu

echo "Starting installation procedure for Davra Agent"
cd "$(dirname "$0")" || exit 1

installationDir="/usr/bin/davra"
venvDir="${installationDir}/.venv"
logFile="/var/log/davra_agent.log"
appGroup="davra-apps"
installOrUpgrade="install" # Is this an installation or upgrade

if [[ $(id -u) -ne 0 ]]; then
    echo "Please run as root"
    exit 1
fi

# The agent requires Python 3.12 or later (Ubuntu 24.04 LTS, Debian 13 or later)
if ! command -v python3 > /dev/null 2>&1; then
    echo "Python3 not found. Installing now."
    apt-get update
    apt-get -y install python3
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "Python 3.12 or later is required. Found: $(python3 --version 2>&1). Upgrade the operating system first."
    exit 1
fi

if [[ ! -d "${installationDir}" ]];
then
    echo "Creating installation directory at ${installationDir}"
    mkdir -p "${installationDir}"
else
    echo "This is an upgrade."
    installOrUpgrade="upgrade"
fi

# Copy the agent code. Code is root-owned and not writable by others.
cp -r ./*.py ./*.sh ./requirements.txt ./davra_agent.service "${installationDir}/"
chown -R root:root "${installationDir}"
chmod 755 "${installationDir}"
chmod 644 "${installationDir}"/*.py "${installationDir}/requirements.txt" "${installationDir}/davra_agent.service"
chmod 755 "${installationDir}"/*.sh
# Working directories and secrets are root only (they may predate this version)
for workDir in currentJob currentFunction; do
    mkdir -p "${installationDir}/${workDir}"
    chmod 700 "${installationDir}/${workDir}"
done
# Installed apps were world-writable in earlier versions
if [[ -d "${installationDir}/apps" ]]; then
    chmod -R go-w "${installationDir}/apps"
fi
if [[ -f "${installationDir}/config.json" ]]; then
    chmod 600 "${installationDir}/config.json"
fi
cd "${installationDir}" || exit 1

echo "Logs going to ${logFile}"
touch "${logFile}"
chown root:adm "${logFile}" 2> /dev/null || chown root:root "${logFile}"
chmod 640 "${logFile}"

echo "Agent install location ${installationDir}"

echo "Running apt-get update"
apt-get update

echo "Installing system requirements"
apt-get -y install --no-install-recommends python3-venv mosquitto
systemctl enable mosquitto.service || echo "WARNING: could not enable the mosquitto service"

# Group whose members (device app service users) may read the device mqtt app credentials
if ! getent group "${appGroup}" > /dev/null; then
    groupadd --system "${appGroup}"
fi

# Install the Python requirements, pinned by hash, into the agent's own virtual environment
echo "Installing Python requirements"
if [[ ! -x "${venvDir}/bin/python" ]]; then
    python3 -m venv "${venvDir}"
fi
"${venvDir}/bin/pip" install --quiet --upgrade --require-hashes --no-deps -r requirements.txt

# Run the python setup script
"${venvDir}/bin/python" -u ./davra_setup.py "${installOrUpgrade}" "$@"
