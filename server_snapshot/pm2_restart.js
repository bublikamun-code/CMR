
const net = require('net');
const socketPath = '/var/www/h212005/data/.pm2/rpc.sock';

const client = net.connect(socketPath, () => {
  console.log('Connected to PM2 socket');
  
  // Send a JSON-RPC request to restart the process
  const msg = JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    method: 'restart',
    params: [{ name: 'crm' }]
  });
  
  // PM2 uses a custom protocol with a prefix
  const prefix = Buffer.from([0, 0, 0, msg.length]);
  client.write(prefix);
  client.write(msg);
});

client.on('data', (data) => {
  console.log('Response:', data.toString());
  client.end();
});

client.on('error', (err) => {
  console.error('Error:', err.message);
  process.exit(1);
});

setTimeout(() => {
  console.log('Timeout - no response');
  process.exit(1);
}, 5000);
