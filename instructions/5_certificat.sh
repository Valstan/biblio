#!/bin/bash

sudo apt -y install python3-certbot-nginx
sudo certbot --nginx -d <server-hostname> -d www.<server-hostname>
