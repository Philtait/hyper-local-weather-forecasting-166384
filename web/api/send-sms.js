/*
 * POST /api/send-sms  (Vercel-style Node serverless function)
 *
 * Sends one Imarika message to the Africa's Talking SANDBOX only.
 * Request JSON: {"phone": "+2547XXXXXXXX", "scenario": "latest" | "crop_guide" | "alert",
 *                "station": "<id>", "lang": "en" | "sw", "crop": "<crop>" (crop_guide only)}
 * Scenario, station, crop and language are checked against the deployed data
 * files (data/stations.json and data/crop_guide.json, bundled with the function
 * by vercel.json), and the text is built here by js/sms-message.js, the same
 * module the page uses for its preview. Text sent by the browser is never used.
 *
 * Environment: AT_USERNAME (must be exactly "sandbox") and AT_API_KEY.
 * The key is never logged or returned.
 *
 * Endpoint and form fields follow the africastalking Python SDK (Service.py,
 * SMS.py): POST https://api.sandbox.africastalking.com/version1/messaging,
 * form-encoded username, to, message, bulkSMSMode=1, with headers apiKey and
 * Accept: application/json.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const builder = require("../js/sms-message.js");

const SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging";
const PHONE = /^\+254[17]\d{8}$/;
const ALLOWED_FIELDS = new Set(["phone", "scenario", "station", "crop", "lang"]);
const RATE_LIMIT = { max: 5, windowMs: 10 * 60 * 1000 };

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function validatePhone(value) {
  const number = String(value == null ? "" : value).replace(/[\s-]/g, "");
  if (!PHONE.test(number)) throw new HttpError(400, "Enter a Kenyan mobile number in +254 format, for example +254712345678");
  return number;
}

function checkConfig(env) {
  if (env.AT_USERNAME !== "sandbox") throw new HttpError(503, "SMS sending is turned off: this demo only runs against the sandbox");
  if (!env.AT_API_KEY) throw new HttpError(503, "SMS sending is not configured");
  return { username: env.AT_USERNAME, apiKey: env.AT_API_KEY };
}

/* Whitelist the request shape; the values are checked against the data in buildText. */
function parseRequest(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new HttpError(400, "Send a JSON object");
  const extra = Object.keys(body).filter((key) => !ALLOWED_FIELDS.has(key));
  if (extra.length) throw new HttpError(400, "Unexpected field: " + extra.join(", "));
  for (const key of Object.keys(body)) {
    if (typeof body[key] !== "string" || body[key].length > 80) throw new HttpError(400, "Invalid value for " + key);
  }
  const phone = validatePhone(body.phone);
  if (!builder.SCENARIOS.includes(body.scenario)) throw new HttpError(400, "Unknown message type");
  if (!builder.LANGS.includes(body.lang)) throw new HttpError(400, "Unknown language");
  if (!body.station) throw new HttpError(400, "Choose a station");
  if (body.scenario === "crop_guide") {
    if (!builder.CROPS.includes(body.crop)) throw new HttpError(400, "Unknown crop");
  } else if (body.crop !== undefined) {
    throw new HttpError(400, "A crop is only used with the crop guide");
  }
  const request = { phone, scenario: body.scenario, station: body.station, lang: body.lang };
  if (body.scenario === "crop_guide") request.crop = body.crop;
  return request;
}

function readJson(file) { return JSON.parse(fs.readFileSync(file, "utf8")); }

function loadData(dataDir, scenario) {
  let stations;
  try { stations = readJson(path.join(dataDir, "stations.json")).stations || []; }
  catch (error) { throw new HttpError(500, "Station data is not available"); }
  let guide = null;
  if (scenario === "crop_guide") {
    try { guide = readJson(path.join(dataDir, "crop_guide.json")); }
    catch (error) { throw new HttpError(500, "The crop guide is not available"); }
  }
  return { stations, guide };
}

/* The station must exist and be sending data; the shared builder makes the exact text. */
function buildText(data, request) {
  const station = builder.findStation(data.stations, request.station);
  if (!station) throw new HttpError(404, "Unknown station");
  if (!builder.selectable(station)) throw new HttpError(409, "This station is not sending data");
  if (request.scenario === "crop_guide" && !builder.stations(data.guide).includes(request.station)) {
    throw new HttpError(409, "There is no crop guide for this station");
  }
  const built = builder.buildMessage(data, request);
  if (!built) throw new HttpError(409, "There is no message to send for this choice");
  if (built.chars > builder.MAX_CHARS) throw new HttpError(500, "Message is longer than two SMS");
  return built.text;
}

function createRateLimiter({ max, windowMs }, now = () => Date.now()) {
  const hits = new Map();
  return function allow(key) {
    const t = now();
    const recent = (hits.get(key) || []).filter((stamp) => t - stamp < windowMs);
    if (recent.length >= max) { hits.set(key, recent); return false; }
    recent.push(t);
    hits.set(key, recent);
    if (hits.size > 5000) hits.clear(); // keep memory bounded on a long-lived instance
    return true;
  };
}

function clientIp(req) {
  const forwarded = (req.headers && (req.headers["x-forwarded-for"] || req.headers["x-real-ip"])) || "";
  return String(forwarded).split(",")[0].trim() || (req.socket && req.socket.remoteAddress) || "unknown";
}

function maskPhone(phone) { return phone.slice(0, 6) + "****" + phone.slice(-2); }

async function readBody(req) {
  if (req.body !== undefined) {
    if (typeof req.body === "string") return JSON.parse(req.body || "null");
    if (Buffer.isBuffer(req.body)) return JSON.parse(req.body.toString("utf8") || "null");
    return req.body;
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 2048) throw new HttpError(413, "Request too large");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "null");
}

async function sendToSandbox(fetchImpl, config, phone, text) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  let response;
  try {
    response = await fetchImpl(SANDBOX_URL, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded", apiKey: config.apiKey },
      body: new URLSearchParams({ username: config.username, to: phone, message: text, bulkSMSMode: "1" }).toString(),
      signal: controller.signal,
    });
  } catch (error) {
    throw new HttpError(502, "The SMS sandbox could not be reached");
  } finally {
    clearTimeout(timer);
  }
  let payload = null;
  try { payload = await response.json(); } catch (error) { payload = null; }
  const recipients = (payload && payload.SMSMessageData && payload.SMSMessageData.Recipients) || [];
  const statuses = recipients.map((item) => item.status);
  if (!response.ok || !statuses.length || statuses.some((status) => status !== "Success")) {
    throw new HttpError(502, "The sandbox did not accept the message" + (statuses.length ? " (" + statuses.join(", ") + ")" : ""));
  }
}

function createHandler({ env = process.env, fetchImpl = globalThis.fetch, dataDir = path.join(__dirname, "..", "data"), limiter } = {}) {
  const allow = limiter || createRateLimiter(RATE_LIMIT);
  return async function handler(req, res) {
    const reply = (status, body) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.setHeader("Cache-Control", "no-store");
      res.end(JSON.stringify(body));
    };
    try {
      if (req.method !== "POST") { res.setHeader("Allow", "POST"); throw new HttpError(405, "Only POST is allowed"); }
      if (!allow(clientIp(req))) throw new HttpError(429, "Too many messages from this connection. Please wait a few minutes");
      let body;
      try { body = await readBody(req); } catch (error) { throw error instanceof HttpError ? error : new HttpError(400, "Send valid JSON"); }
      const request = parseRequest(body);
      const config = checkConfig(env);
      const text = buildText(loadData(dataDir, request.scenario), request);
      await sendToSandbox(fetchImpl, config, request.phone, text);
      reply(200, { ok: true, mode: "sandbox", detail: "accepted by the sandbox for " + maskPhone(request.phone),
                   chars: text.length, parts: builder.smsParts(text.length) });
    } catch (error) {
      const status = error instanceof HttpError ? error.status : 500;
      const message = error instanceof HttpError ? error.message : "Something went wrong";
      reply(status, { ok: false, error: message });
    }
  };
}

module.exports = createHandler();
module.exports.createHandler = createHandler;
module.exports.validatePhone = validatePhone;
module.exports.checkConfig = checkConfig;
module.exports.parseRequest = parseRequest;
module.exports.buildText = buildText;
module.exports.createRateLimiter = createRateLimiter;
module.exports.RATE_LIMIT = RATE_LIMIT;
module.exports.DATA_DIR = path.join(__dirname, "..", "data");
module.exports.SANDBOX_URL = SANDBOX_URL;
