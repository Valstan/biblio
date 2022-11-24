#!/bin/bash

sudo apt -y update
sudo apt -y install python3-pip python3-dev build-essential libssl-dev libffi-dev python3-setuptools
cd /home/valstan
git clone http://github.com/name_project.git
cd /home/valstan/name_project
nano config.py
python3 -m venv
source venv/bin/activate
pip install wheel
pip install -r requirements.txt
