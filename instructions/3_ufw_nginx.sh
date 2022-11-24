#!/bin/bash

sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status

#Установка Nginx
sudo apt -y update
sudo apt -y install nginx
sudo ufw app list
sudo ufw allow 'Nginx HTTP'
sudo ufw status