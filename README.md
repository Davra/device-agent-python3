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
