const net = require('net'), fs = require('fs'), dns = require('dns'), dgram = require('dgram');
const http = require('http');
const errName = (e) => ({ECONNREFUSED: 'ConnectionRefusedError'}[e.code] || e.code || e.name);
const redact = (text, c) => {
  if (!c.auth_env) return text;
  const value = process.env[c.auth_env] || '';
  for (const part of [value, value.length >= 8 ? value.slice(-4) : '']) {
    if (part) text = text.split(part).join('<redacted>');
  }
  return text.replace(/sk-[A-Za-z0-9_-]+/g, 'sk-<redacted>').replace(/[0-9a-fA-F]{32,}/g, '<redacted>');
};
const kinds = {
  tcp: (c) => new Promise((resolve) => {
    const s = net.connect({host: c.host, port: c.port});
    const t = setTimeout(() => { s.destroy(); resolve({error: 'TimeoutError'}); }, (c.timeout || 3) * 1000);
    s.on('connect', () => { clearTimeout(t); s.destroy(); resolve({connected: true}); });
    s.on('error', (e) => { clearTimeout(t); resolve({error: errName(e)}); });
  }),
  http: (c) => new Promise((resolve) => {
    const headers = Object.assign({'Content-Type': 'application/json'}, c.headers || {});
    if (c.auth_env) headers['Authorization'] = 'Bearer ' + process.env[c.auth_env];
    const body = c.body === undefined || c.body === null ? null : JSON.stringify(c.body);
    const req = http.request(c.url, {method: body === null ? 'GET' : 'POST', headers, timeout: (c.timeout || 5) * 1000}, (res) => {
      const chunks = [];
      res.on('data', (d) => chunks.push(d));
      res.on('end', () => resolve({status: res.statusCode, head: redact(Buffer.concat(chunks).toString().slice(0, 120), c)}));
    });
    req.on('timeout', () => { req.destroy(); resolve({error: 'TimeoutError'}); });
    req.on('error', (e) => resolve({error: errName(e)}));
    if (body !== null) req.write(body);
    req.end();
  }),
  path: (c) => ({exists: fs.existsSync(c.path)}),
  env_names: () => ({names: Object.keys(process.env).sort()}),
  read: (c) => {
    try { return {text: fs.readFileSync(c.path).toString().slice(0, c.size || 4096)}; }
    catch (e) { return {error: errName(e)}; }
  },
  write: (c) => {
    try { fs.writeFileSync(c.path, 'poc06'); } catch (e) { return {error: errName(e)}; }
    fs.unlinkSync(c.path);
    return {written: true};
  },
  resolve: (c) => new Promise((resolve) => {
    dns.lookup(c.name, {all: true}, (e, a) => resolve(e ? {error: errName(e)} : {addresses: a.map((x) => x.address).sort()}));
  }),
  dns: (c) => new Promise((resolve) => {
    const parts = c.name.split('.');
    const head = Buffer.from([0x50, 0x06, 0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0]);
    const labels = Buffer.concat(parts.map((p) => Buffer.concat([Buffer.from([p.length]), Buffer.from(p)])));
    const q = Buffer.concat([head, labels, Buffer.from([0, 0, 1, 0, 1])]);
    const s = dgram.createSocket('udp4');
    const t = setTimeout(() => { s.close(); resolve({error: 'TimeoutError'}); }, (c.timeout || 3) * 1000);
    s.on('message', (m) => { clearTimeout(t); s.close(); resolve({rcode: m.readUInt16BE(2) & 0xf, answers: m.readUInt16BE(6)}); });
    s.on('error', (e) => { clearTimeout(t); resolve({error: errName(e)}); });
    s.send(q, 53, c.server);
  }),
};
(async () => {
  const out = [];
  for (const c of JSON.parse(process.argv[1])) out.push(await kinds[c.kind](c));
  console.log(JSON.stringify(out));
})();
