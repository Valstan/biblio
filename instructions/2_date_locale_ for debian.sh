#!/bin/bash

sudo dpkg-reconfigure locales
timedatectl list-timezones | grep Moscow
sudo timedatectl set-timezone Europe/Moscow
timedatectl
sudo apt-get install ntp -y
date
