#!/bin/bash

sudo apt -y install python3-certbot-nginx
sudo certbot --nginx -d ovz4.id45d.m61kn.vps.myjino.ru -d www.ovz4.id45d.m61kn.vps.myjino.ru
