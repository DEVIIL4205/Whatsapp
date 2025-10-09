// server.js (ESM)
import express from "express";
import multer from "multer";
import fs from "fs";
import path from "path";
import qrcode from "qrcode";
import { Boom } from "@hapi/boom";
import makeWASocket, {
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  DisconnectReason
} from "@whiskeysockets/baileys";

const app = express();
const upload = multer({ dest: "./uploads" });

app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(express.static("public"));

/* ---------------------------
   In-memory status & logs
   --------------------------- */
let sock = null;
let lastQRDataUrl = null;
let paired = false;
let sendingTask = null;
let lastLogs = [];
const pushLog = (t) => {
  const ts = new Date().toLocaleString();
  lastLogs.unshift(`[${ts}] ${t}`);
  if (lastLogs.length > 200) lastLogs.pop();
};

/* ---------------------------
   Create / ensure session dir
   --------------------------- */
const SESS_DIR = "./session";
if (!fs.existsSync(SESS_DIR)) fs.mkdirSync(SESS_DIR);

/* ---------------------------
   Connect / (re)connect to WA
   --------------------------- */
async function startSocket() {
  try {
    const { state, saveCreds } = await useMultiFileAuthState(SESS_DIR);
    const { version } = await fetchLatestBaileysVersion();
    pushLog("Starting WhatsApp socket (fetching version: " + version.join('.') + ")");

    sock = makeWASocket({
      version,
      auth: state,
      printQRInTerminal: false,
      logger: { level: "silent" }
    });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", async (update) => {
      // update may contain { qr } or connection state
      const { connection, lastDisconnect, qr } = update;
      if (qr) {
        // produce DataURL for frontend
        try {
          lastQRDataUrl = await qrcode.toDataURL(qr);
          pushLog("New QR generated.");
        } catch (e) {
          pushLog("QR -> DataURL error: " + e.message);
        }
      }
      if (connection) pushLog("Connection update: " + connection);
      if (connection === "close") {
        // decide to reconnect
        const code = (lastDisconnect?.error && lastDisconnect.error.output?.statusCode) || lastDisconnect?.error?.statusCode;
        pushLog("Connection closed: " + JSON.stringify(lastDisconnect?.error?.message || lastDisconnect));
        if (code !== DisconnectReason.loggedOut) {
          pushLog("Reconnecting...");
          lastQRDataUrl = null;
          setTimeout(() => startSocket(), 1500);
        } else {
          pushLog("Logged out (remove session to re-pair).");
          paired = false;
        }
      }
      if (connection === "open") {
        pushLog("WhatsApp connected/open.");
        lastQRDataUrl = null;
        paired = true;
        // optionally prefetch groups
      }
    });

    pushLog("Socket started.");
  } catch (err) {
    pushLog("startSocket error: " + (err.message || err));
    // try again slower
    setTimeout(startSocket, 5000);
  }
}

// start initial socket (will produce QR if no creds)
startSocket();

/* ---------------------------
   API: get QR data URL (frontend polls)
   --------------------------- */
app.get("/qr", (req, res) => {
  if (!lastQRDataUrl) return res.json({ ok: false, msg: "no-qr" });
  return res.json({ ok: true, dataUrl: lastQRDataUrl });
});

/* ---------------------------
   API: get minimal status
   --------------------------- */
app.get("/status", (req, res) => {
  res.json({
    connected: !!(sock && paired),
    hasQR: !!lastQRDataUrl
  });
});

/* ---------------------------
   API: fetch groups (requires paired)
   --------------------------- */
app.get("/groups", async (req, res) => {
  try {
    if (!sock || !paired) return res.status(400).json({ ok: false, msg: "Not paired/connected" });
    const groups = await sock.groupFetchAllParticipating();
    // map to array
    const arr = Object.values(groups).map(g => ({ id: g.id, subject: g.subject || "(no-name)" }));
    res.json({ ok: true, groups: arr });
  } catch (err) {
    pushLog("groups error: " + err.message);
    res.status(500).json({ ok: false, error: err.message });
  }
});

/* ---------------------------
   API: start sending
   Accepts multipart form:
   - targetType: "number" or "group"
   - targetValue: number (E.164 w/o @s.whatsapp.net) OR group jid (full jid like 1203...@g.us)
   - hetter: optional text prefix
   - delayMs: integer (ms)
   - file (optional) : .txt file (lines)
   - text (optional) : single message textarea
   Response: stopKey (server generated)
   --------------------------- */
app.post("/start", upload.single("file"), async (req, res) => {
  try {
    if (!sock || !paired) return res.status(400).json({ ok: false, msg: "Not paired/connected" });

    const { targetType, targetValue, hetter, delayMs, text } = req.body;
    let delay = parseInt(delayMs) || 5000;

    // determine jid
    let jid = "";
    if (targetType === "number") {
      const normalized = targetValue.replace(/\D/g, "");
      jid = normalized + "@s.whatsapp.net";
    } else { // assume group id provided as full jid or raw id
      jid = targetValue.includes("@g.us") ? targetValue : (targetValue + "@g.us");
    }

    // build messages array
    let messages = [];
    if (req.file) {
      const filePath = req.file.path;
      const content = fs.readFileSync(filePath, "utf8");
      messages = content.split(/\r?\n/).map(l=>l.trim()).filter(Boolean);
      // remove uploaded file after reading
      fs.unlinkSync(filePath);
    }
    if (text && text.trim()) messages.push(text.trim());
    if (messages.length === 0) messages = [`${hetter || ""} Hello`];

    // create stopKey
    const stopKey = Math.random().toString(36).slice(2, 10);
    pushLog(`Start requested -> target=${jid} delay=${delay}ms messages=${messages.length} stopKey=${stopKey}`);

    // ensure previous sending stopped
    if (sendingTask && sendingTask.running) {
      sendingTask.running = false;
      pushLog("Stopped previous sending task.");
    }

    // start async loop
    const task = { running: true };
    sendingTask = task;

    (async () => {
      let i = 0;
      while (task.running) {
        const msg = messages[i % messages.length];
        const sendText = (hetter ? `[${hetter}] ` : "") + msg;
        try {
          await sock.sendMessage(jid, { text: sendText });
          pushLog(`Sent -> ${jid} : ${sendText}`);
        } catch (err) {
          pushLog("Send error: " + (err.message || err));
        }
        i++;
        // wait
        await new Promise(r => setTimeout(r, delay));
      }
      pushLog("Sending task stopped.");
    })();

    // store stopKey associated with current task
    task.stopKey = stopKey;
    res.json({ ok: true, stopKey });
  } catch (err) {
    pushLog("start API error: " + (err.message || err));
    res.status(500).json({ ok: false, error: err.message });
  }
});

/* ---------------------------
   API: stop by key
   --------------------------- */
app.post("/stop", (req, res) => {
  try {
    const { stopKey } = req.body;
    if (!sendingTask || !sendingTask.running) return res.json({ ok: true, msg: "No running task" });
    if (sendingTask.stopKey && stopKey === sendingTask.stopKey) {
      sendingTask.running = false;
      pushLog("Stop called with correct key -> stopping.");
      return res.json({ ok: true, msg: "Stopped" });
    }
    return res.status(400).json({ ok: false, msg: "Invalid stop key" });
  } catch (err) {
    pushLog("stop API error: " + err.message);
    res.status(500).json({ ok: false, error: err.message });
  }
});

/* ---------------------------
   API: logs
   --------------------------- */
app.get("/logs", (req, res) => {
  res.json({ ok: true, logs: lastLogs });
});

/* ---------------------------
   Start server
   --------------------------- */
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
  pushLog("Server started on port " + PORT);
});