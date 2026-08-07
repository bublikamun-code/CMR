<?php
if (isset($_GET['key']) && $_GET['key'] === 'crm_restart_2026') {
    $output = [];
    $exit_code = 0;
    $NVM_DIR = "/var/www/h212005/data/.nvm";
    $PM2_BIN = "/var/www/h212005/data/.nvm/versions/node/v24.18.0/bin/pm2";
    exec("export NVM_DIR=$NVM_DIR && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && pm2 restart crm 2>&1", $output, $exit_code);
    echo json_encode(['exit_code' => $exit_code, 'output' => $output]);
} else {
    echo json_encode(['error' => 'Invalid key']);
}
?>