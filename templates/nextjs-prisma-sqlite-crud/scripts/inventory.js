const fs = require('fs');
const path = require('path');

function main() {
  const serverPath = path.join(__dirname, '..', 'server.js');
  const source = fs.readFileSync(serverPath, 'utf-8');
  const endpoints = [];

  const compareRegex = /req\.method\s*===\s*"([A-Z]+)"\s*&&\s*url\.pathname\s*===\s*"([^"]+)"/g;
  for (const match of source.matchAll(compareRegex)) {
    endpoints.push({ method: match[1], path: match[2] });
  }

  const startsRegex = /req\.method\s*===\s*"([A-Z]+)"\s*&&\s*url\.pathname\.startsWith\(\s*"([^"]+)"\s*\)/g;
  for (const match of source.matchAll(startsRegex)) {
    const base = match[2].endsWith('/') ? match[2] + '{param}' : match[2];
    endpoints.push({ method: match[1], path: base });
  }

  const dedupe = new Map();
  for (const endpoint of endpoints) {
    dedupe.set(`${endpoint.method} ${endpoint.path}`, endpoint);
  }

  const payload = {
    endpoints: Array.from(dedupe.values()).sort((a, b) => {
      if (a.path === b.path) return a.method.localeCompare(b.method);
      return a.path.localeCompare(b.path);
    }),
  };
  process.stdout.write(JSON.stringify(payload));
}

main();
