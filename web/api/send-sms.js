/*
 * POST /api/send-sms  (Vercel-style Node serverless function)
 *
 * Sends one Imarika advisory to the Africa's Talking SANDBOX only.
 * Request JSON: {"phone": "+2547XXXXXXXX", "station": "<id>", "advisory": "<key>"}
 *           or: {"phone": "+2547XXXXXXXX", "scenario": "<key>"}
 * The message text is looked up here from the deployed data files; text sent
 * by the browser is never used.
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

const SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging";
const PHONE = /^\+254[17]\d{8}$/;
const ALLOWED_FIELDS = new Set(["phone", "station", "advisory", "scenario"]);
const MAX_CHARS = 160;
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

function parseRequest(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new HttpError(400, "Send a JSON object");
  const extra = Object.keys(body).filter((key) => !ALLOWED_FIELDS.has(key));
  if (extra.length) throw new HttpError(400, "Unexpected field: " + extra.join(", "));
  for (const key of Object.keys(body)) {
    if (typeof body[key] !== "string" || body[key].length > 80) throw new HttpError(400, "Invalid value for " + key);
  }
  const phone = validatePhone(body.phone);
  if (body.scenario && (body.station || body.advisory)) throw new HttpError(400, "Send either a scenario or a station advisory, not both");
  if (body.scenario) return { phone, scenario: body.scenario };
  if (body.station && body.advisory) return { phone, station: body.station, advisory: body.advisory };
  throw new HttpError(400, "Choose a station advisory or a scenario");
}

function readJson(file) { return JSON.parse(fs.readFileSync(file, "utf8")); }

function loadData(dataDir) {
  return { stations: readJson(path.join(dataDir, "stations.json")).stations || [],
           scenarios: readJson(path.join(dataDir, "scenarios.json")).scenarios || [] };
}

function lookupMessage(data, request) {
  let text = null;
  if (request.scenario) {
    const scenario = data.scenarios.find((item) => item.key === request.scenario);
    if (!scenario) throw new HttpError(404, "Unknown scenario");
    text = scenario.sms_text;
  } else {
    const station = data.stations.find((item) => item.id === request.station);
    if (!station) throw new HttpError(404, "Unknown station");
    const insight = (station.insights || []).find((item) => item.key === request.advisory);
    if (!insight) throw new HttpError(404, "Unknown advisory for this station");
    text = insight.sms_text;
  }
  if (!text) throw new HttpError(409, "There is no message to send for this choice");
  if (text.length > MAX_CHARS) throw new HttpError(500, "Message is longer than one SMS");
  return text;
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
      let data;
      try { data = loadData(dataDir); } catch (error) { throw new HttpError(500, "Forecast data is not available"); }
      const text = lookupMessage(data, request);
      await sendToSandbox(fetchImpl, config, request.phone, text);
      reply(200, { ok: true, mode: "sandbox", detail: "accepted by the sandbox for " + maskPhone(request.phone), chars: text.length });
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
module.exports.lookupMessage = lookupMessage;
module.exports.createRateLimiter = createRateLimiter;
module.exports.SANDBOX_URL = SANDBOX_URL;
