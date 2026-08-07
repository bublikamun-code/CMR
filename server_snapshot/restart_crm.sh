#!/bin/bash
export NVM_DIR=/Users/aroslav/.nvm
[ -s /nvm.sh ] && \. /nvm.sh
pm2 restart crm 2>&1 || pm2 start /var/www/h212005/data/www/cmr-svetvdome.online/server.py --name crm
pm2 save 2>&1
