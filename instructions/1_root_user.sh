#!/bin/bash

#chmod +x ./myscript

adduser valstan
usermod -aG sudo valstan
apt -y update
apt -y upgrade
apt -y install mc ufw nano git tmux htop
