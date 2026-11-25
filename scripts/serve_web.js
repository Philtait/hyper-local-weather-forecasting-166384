/* Local test server for the website with the real SMS function (no dependencies, local use only).

     node --env-file=.env scripts/serve_web.js            then open http://127.0.0.1:8080/sms.html

   Serves web/ as static files and routes POST /api/send-sms to web/api/send-sms.js, the same handler
   Vercel deploys. The handler reads AT_USERNAME (must be exactly "sandbox") and AT_API_KEY from the
   environment; this server never prints them. Listens on 127.0.0.1 only. */
"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const WEB = path.resolve(__dirname, "..", "web");
const PORT = Number(process.env.PORT) || 8080;
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css",
  ".json": "application/json", ".geojson": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
  ".webp": "image/webp", ".woff2": "font/woff2", ".webmanifest": "application/manifest+json", ".txt": "text/plain" };
const sendSms = require(path.join(WEB, "api", "send-sms.js"));

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/api/send-sms") return sendSms(req, res);
  const file = path.normalize(path.join(WEB, decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname)));
  if (!file.startsWith(WEB + path.sep) || file.startsWith(path.join(WEB, "api") + path.sep)) {
    res.statusCode = 404;
    return res.end("Not found");
  }
  fs.readFile(file, (error, data) => {
    if (error) { res.statusCode = 404; return res.end("Not found"); }
    res.setHeader("Content-Type", TYPES[path.extname(file)] || "application/octet-stream");
    res.end(data);
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`Imarika site with the sandbox SMS function: http://127.0.0.1:${PORT}/sms.html\n`);
});
