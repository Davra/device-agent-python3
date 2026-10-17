#!/bin/bash

echo "Starting UN-install procedure for Davra Agent"
cd "$(dirname "$0")" || exit 1

installationDir="/usr/bin/davra"

if [[ $(id -u) -ne 0 ]]; then
    echo "Please run as root"
    exit 1
fi


systemctl stop davra_agent.service
systemctl disable davra_agent.service

rm -f /lib/systemd/system/davra_agent.service

systemctl daemon-reload


mv "${installationDir}/davra_agent.py" "${installationDir}/davra_agent_UNINSTALLED.py"

# Remove the device mqtt broker settings and credentials created by setup
rm -f /etc/mosquitto/conf.d/davra.conf /etc/mosquitto/davra_passwd /etc/mosquitto/davra_acl \
    "${installationDir}/app_mqtt_credentials.json"
systemctl restart mosquitto 2> /dev/null

logFile="/var/log/davra_agent.log"
mv "${logFile}" "${logFile}_UNINSTALLED"

echo "Finished UN-install procedure for Davra Agent"
