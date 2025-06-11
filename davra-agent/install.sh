#!/bin/bash

echo "Starting installation procedure for Davra Agent"
cd `dirname $0`

installationDir="/usr/bin/davra"
installOrUpgrade="install" # Is this an installation or upgrade

if [[ $(id -u) -ne 0 ]]; then
    echo "Please run as root" 
    exit 1 
fi

if [[ ! -d "${installationDir}" ]]; 
then
	echo "Creating installation directory at ${installationDir}"
	mkdir -p "${installationDir}"
    mkdir -p "${installationDir}/currentJob"
else 
    echo "This is an upgrade."
    installOrUpgrade="upgrade"
fi 

# echo "Setting file permissons ..."
cp -r . "${installationDir}"
chmod -R 755 "${installationDir}"
cd "${installationDir}"

logFile="/var/log/davra_agent.log"
echo "Logs going to ${logFile}"
touch "${logFile}"
chmod 777 "${logFile}"

# Confirm now in the installation directory
if [[ $(pwd) != "${installationDir}" ]]; then
    echo "Could not create and navigate to ${installationDir}" 
    exit 1 
fi

echo "Agent install location ${installationDir}"

echo "Running apt-get update"
sudo apt-get update

# Install CUrl for application extensions
echo "Installing extension requirements"
sudo apt-get -y install curl

# Confirm python available
if which python3 > /dev/null 2>&1;
then
    echo "Python 3 found ok."
else 
    echo "Python3 not found. Installing now."
    sudo apt-get -y install python3
fi

sudo rm /usr/lib/python3.11/EXTERNALLY-MANAGED

# Confirm required Python libraries available
# If you add new libraries to the agent, update requirements.txt
echo "Installing Python requirements"
sudo apt-get -y install python3-pip
sudo apt-get install -y python-setuptools
pip3 install -r requirements.txt


# Confirm there is an MQTT Broker running on this device
if which mosquitto > /dev/null 2>&1;
then
    echo "MQTT broker found ok."
else 
    echo "MQTT broker not found. Installing now."
    sudo apt install -y mosquitto 
    sudo systemctl enable mosquitto.service
fi


# Run the python setup script
python3 -u ./davra_setup.py "${installOrUpgrade}" $@ 