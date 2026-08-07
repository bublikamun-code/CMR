<?php
// Restart PM2 crm process
$output = [];
$exit_code = 0;

// Find nvm and pm2
$NVM_DIR = "/var/www/h212005/data/.nvm";
$PM2_BIN = "/var/www/h212005/data/.nvm/versions/node/v24.18.0/bin/pm2";

// Activate nvm
$nvm_output = [];
exec("export NVM_DIR=$NVM_DIR && [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\" && pm2 restart crm 2>&1", $output, $exit_code);

echo "Exit code: $exit_code\n";
echo "Output:\n" . implode("\n", $output) . "\n";

// Also try direct path
if ($exit_code != 0) {
    exec("$PM2_BIN restart crm 2>&1", $output2, $exit_code2);
    echo "Direct PM2 attempt:\nExit code: $exit_code2\n";
    echo "Output:\n" . implode("\n", $output2) . "\n";
}
?>
