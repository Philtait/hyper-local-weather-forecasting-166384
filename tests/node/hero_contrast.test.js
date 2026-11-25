// node --test tests/node/hero_contrast.test.js   (local only: a static server on 127.0.0.1 and a headless browser)
// Renders Home and the SMS demo at 1440 x 900 and 390 x 844, hides the hero text, screenshots the hero and
// samples the real pixels under every line of white text: the lightest pixel must still give 4.5:1 against
// white (WCAG AA). Also checks that no page overflows sideways at either width.
// Skips when no Edge or Chrome is installed (set CHROME_PATH to point at one).
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const zlib = require("node:zlib");
const { spawn } = require("node:child_process");

const WEB = path.join(__dirname, "../../web");
const PAGES = ["index.html", "how-it-works.html", "map.html", "station.html?id=336", "sms.html", "contact.html"];
const SIZES = [[1440, 900], [390, 844]];
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".geojson": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp",
  ".woff2": "font/woff2", ".webmanifest": "application/manifest+json" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function browserPath() {
  const candidates = [process.env.CHROME_PATH,
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "/usr/bin/google-chrome", "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"];
  return candidates.find((p) => p && fs.existsSync(p)) || null;
}

/* A minimal PNG reader for the browser's screenshots: 8-bit RGB or RGBA, not interlaced. */
function decodePng(buffer) {
  let pos = 8, width = 0, height = 0, type = 0;
  const idat = [];
  while (pos < buffer.length) {
    const len = buffer.readUInt32BE(pos), kind = buffer.toString("ascii", pos + 4, pos + 8), data = buffer.subarray(pos + 8, pos + 8 + len);
    if (kind === "IHDR") { width = data.readUInt32BE(0); height = data.readUInt32BE(4); type = data[9]; assert.equal(data[8], 8); assert.equal(data[12], 0); }
    if (kind === "IDAT") idat.push(data);
    pos += 12 + len;
  }
  const bpp = type === 6 ? 4 : 3, stride = width * bpp, raw = zlib.inflateSync(Buffer.concat(idat)), out = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)], line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? out[y * stride + x - bpp] : 0, b = y ? out[(y - 1) * stride + x] : 0, c = x >= bpp && y ? out[(y - 1) * stride + x - bpp] : 0;
      const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
      const pred = [0, a, b, (a + b) >> 1, pa <= pb && pa <= pc ? a : pb <= pc ? b : c][filter];
      out[y * stride + x] = (line[x] + pred) & 255;
    }
  }
  return { width, height, bpp, pixels: out };
}

function luminance(r, g, b) {
  const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function serve() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    const file = path.normalize(path.join(WEB, decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname)));
    if (!file.startsWith(WEB)) { res.statusCode = 404; return res.end(); }
    fs.readFile(file, (error, data) => {
      if (error) { res.statusCode = 404; return res.end(); }
      res.setHeader("Content-Type", TYPES[path.extname(file)] || "application/octet-stream");
      res.end(data);
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

async function openBrowser(exe) {
  const port = 9400 + Math.floor(Math.random() * 400);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "imarika-hero-"));
  const proc = spawn(exe, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--no-first-run",
    "--disable-gpu", "about:blank"], { stdio: "ignore" });
  let target;
  for (let i = 0; i < 75 && !target; i++) {
    await sleep(200);
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); } catch (e) { /* starting */ }
  }
  if (!target) { proc.kill(); throw new Error("the browser did not start"); }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  const send = (method, params = {}) => new Promise((resolve) => { id += 1; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expression) => (await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result.result.value;
  await send("Network.enable");
  await send("Network.setBypassServiceWorker", { bypass: true });
  await send("Network.setCacheDisabled", { cacheDisabled: true });
  return { send, evaluate, close: () => { ws.close(); proc.kill(); } };
}

async function load(b, base, page, width, height) {
  await b.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 800 });
  await b.send("Page.navigate", { url: "about:blank" });
  await sleep(200);
  await b.send("Page.navigate", { url: base + page });
  for (let i = 0; i < 50; i++) { await sleep(200); if (await b.evaluate("document.readyState === 'complete' && document.fonts.status === 'loaded'")) break; }
  await sleep(1500);   // data and the status line
}

const exe = browserPath();

test("hero text keeps at least 4.5:1 against the real image, and no page overflows sideways", { skip: !exe && "no Edge or Chrome found" }, async () => {
  const server = await serve();
  const base = `http://127.0.0.1:${server.address().port}/`;
  const b = await openBrowser(exe);
  const results = [];
  try {
    for (const [width, height] of SIZES) {
      for (const page of PAGES) {
        await load(b, base, page, width, height);
        const overflow = await b.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth");
        assert.equal(overflow, false, `${page} overflows sideways at ${width} px`);
        if (!["index.html", "sms.html"].includes(page)) continue;
        // Rectangles of every line of white text in the hero (the primary button has its own white fill).
        const rects = await b.evaluate(`(() => {
          const out = [];
          const walk = document.createTreeWalker(document.querySelector('.hero-copy'), NodeFilter.SHOW_TEXT);
          for (let n = walk.nextNode(); n; n = walk.nextNode()) {
            if (!n.textContent.trim() || n.parentElement.closest('.btn-primary, [hidden]')) continue;
            const range = document.createRange(); range.selectNodeContents(n);
            for (const r of range.getClientRects()) if (r.width > 2 && r.height > 2)
              out.push({ x: Math.ceil(r.left), y: Math.ceil(r.top), w: Math.floor(r.width), h: Math.floor(r.height), text: n.textContent.trim().slice(0, 30) });
          }
          return out;
        })()`);
        assert.ok(rects.length >= 2, `${page}: no hero text found`);
        // Hide the text, keep everything else, and screenshot what sits behind it.
        await b.evaluate(`(() => { const s = document.createElement('style');
          s.textContent = '.hero-copy, .hero-copy * { color: transparent !important; text-shadow: none !important; } .hero-copy svg { visibility: hidden; }';
          document.head.appendChild(s); return true; })()`);
        await sleep(300);
        const shot = await b.send("Page.captureScreenshot", { format: "png" });
        const img = decodePng(Buffer.from(shot.result.data, "base64"));
        for (const r of rects) {
          let worst = 0;
          for (let y = r.y; y < Math.min(r.y + r.h, img.height); y++) {
            for (let x = r.x; x < Math.min(r.x + r.w, img.width); x++) {
              const i = (y * img.width + x) * img.bpp;
              worst = Math.max(worst, luminance(img.pixels[i], img.pixels[i + 1], img.pixels[i + 2]));
            }
          }
          const ratio = 1.05 / (worst + 0.05);
          results.push({ page, width, text: r.text, ratio: Math.round(ratio * 100) / 100 });
          assert.ok(ratio >= 4.5, `${page} at ${width} px: "${r.text}" is ${ratio.toFixed(2)}:1 against the lightest pixel behind it`);
        }
      }
    }
  } finally {
    b.close();
    server.close();
  }
  if (process.env.HERO_REPORT) console.log(JSON.stringify(results));
});
