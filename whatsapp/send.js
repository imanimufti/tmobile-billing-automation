#!/usr/bin/env node
/*
 * Headless WhatsApp sender for the T-Mobile bill pipeline.
 *
 * Uses Baileys (@whiskeysockets/baileys), which speaks WhatsApp's WebSocket
 * protocol directly rather than automating WhatsApp Web in a browser. This
 * avoids the "WhatsApp Web version drift" class of breakage that stalled the
 * old whatsapp-web.js path, and still supports posting to group chats — for
 * which WhatsApp has no official API.
 *
 * Session persists under state/whatsapp_baileys/ (gitignored) as a multi-file
 * auth store. Seed it once by scanning a QR; later runs reuse the credentials
 * and work while the Mac is locked.
 *
 * CLI contract (unchanged from the old sender — callers parse this output):
 *   node send.js seed                          # one-time: scan the QR with your phone
 *   node send.js list-groups                   # print "<name>  [<jid>]" per group
 *   node send.js list-members --group-id <jid> # print "<user>\t<name>" per participant
 *   node send.js send --group-id <jid> --message-file <path> [--image <path>]
 *   node send.js send --group "<name>" --message "<text>"
 *   node send.js send --to <number> --message "<text>" [--image <path>]
 *   node send.js send-batch --file <json>      # [{to, message, image?}, ...]
 *
 * Exit codes: 0 ok · 1 generic · 2 timeout · 3 not logged in/auth · 4 partial batch.
 */

const path = require('path');
const fs = require('fs');
const os = require('os');
const { exec } = require('child_process');
const {
  makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  Browsers,
} = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const QRImage = require('qrcode');

const DATA_PATH = path.join(__dirname, '..', 'state', 'whatsapp_baileys');

// Baileys expects a pino-like logger; a silent stub keeps the protocol chatter
// out of stdout (callers parse stdout) without pulling in pino.
const logger = {
  level: 'silent',
  trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {},
  child() { return logger; },
};

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) {
      const key = argv[i].slice(2);
      const val = (i + 1 < argv.length && !argv[i + 1].startsWith('--')) ? argv[++i] : true;
      args[key] = val;
    }
  }
  return args;
}

function fail(msg, code = 1) {
  console.error('✗ ' + msg);
  process.exit(code);
}

function getMessageText(args) {
  if (args['message-file']) return fs.readFileSync(args['message-file'], 'utf8');
  if (args.message) return String(args.message);
  return '';
}

// --- QR rendering (seed only): terminal + a crisp PNG opened full-size -------
let qrShown = false;
function showQr(qr) {
  console.log('\nScan in WhatsApp → Settings → Linked Devices → Link a Device.');
  qrcode.generate(qr, { small: true });
  const qrImgPath = path.join(os.tmpdir(), 'tmobile-whatsapp-qr.png');
  QRImage.toFile(qrImgPath, qr, { width: 500, margin: 3 }, (err) => {
    if (err) return;
    if (!qrShown) {
      qrShown = true;
      exec(`open "${qrImgPath}"`);
      console.log(`\n→ A scannable QR image opened on screen: ${qrImgPath}`);
      console.log('  (If it expires before you scan, just re-run `node send.js seed`.)');
    }
  });
}

// --- helpers over a connected socket -----------------------------------------
const userJid = (num) => `${String(num).replace(/\D/g, '')}@s.whatsapp.net`;

async function resolveGroupJid(sock, args) {
  if (args['group-id']) return String(args['group-id']);
  if (args.group) {
    const groups = await sock.groupFetchAllParticipating();
    const want = String(args.group).trim().toLowerCase();
    for (const [jid, meta] of Object.entries(groups)) {
      if ((meta.subject || '').trim().toLowerCase() === want) return jid;
    }
    const listing = Object.entries(groups)
      .map(([jid, meta]) => `${meta.subject}  [${jid}]`).join('\n  ');
    fail(`Group "${args.group}" not found. Available groups:\n  ${listing}`);
  }
  fail('Specify --group "<name>" or --group-id "<id>"');
}

async function sendToJid(sock, jid, text, imagePath) {
  if (imagePath) {
    await sock.sendMessage(jid, { image: fs.readFileSync(imagePath), caption: text || '' });
  } else {
    await sock.sendMessage(jid, { text: text || '' });
  }
}

async function runCommand(sock, cmd, args) {
  if (cmd === 'seed') {
    console.log('✓ WhatsApp session is ready and saved. You can close this.');
    return 0;
  }
  if (cmd === 'list-groups') {
    const groups = await sock.groupFetchAllParticipating();
    for (const [jid, meta] of Object.entries(groups)) {
      console.log(`${meta.subject}  [${jid}]`);
    }
    return 0;
  }
  if (cmd === 'list-members') {
    const jid = await resolveGroupJid(sock, args);
    const meta = await sock.groupMetadata(jid);
    for (const p of meta.participants) {
      const user = (p.id || '').split('@')[0];
      console.log(`${user}\t${p.notify || ''}`);
    }
    return 0;
  }
  if (cmd === 'send-batch') {
    // Send several DMs in one session. --file points to a JSON array of
    // {to, message, image?}. Prints "✓ DM sent to <number>" per success so the
    // caller (run_pipeline reminder stage) can record exactly who was reminded.
    const items = JSON.parse(fs.readFileSync(args.file, 'utf8'));
    let ok = 0;
    for (const it of items) {
      const num = String(it.to).replace(/\D/g, '');
      try {
        const res = await sock.onWhatsApp(num);
        if (!res || !res[0] || !res[0].exists) { console.log(`✗ ${num} not on WhatsApp`); continue; }
        await sendToJid(sock, res[0].jid, it.message || '', it.image);
        console.log(`✓ DM sent to ${num}`);
        ok++;
      } catch (e) {
        console.log(`✗ ${it.to}: ${e && e.message ? e.message : e}`);
      }
    }
    console.log(`Batch done: ${ok}/${items.length} sent`);
    return ok < items.length ? 4 : 0;
  }
  if (cmd === 'send') {
    const text = getMessageText(args);
    if (args.to) {
      const num = String(args.to).replace(/\D/g, '');
      const res = await sock.onWhatsApp(num);
      if (!res || !res[0] || !res[0].exists) fail(`Number ${args.to} is not on WhatsApp`);
      await sendToJid(sock, res[0].jid, text, args.image);
      console.log(`✓ Sent ${args.image ? 'image + caption' : 'message'} to "${num}"`);
    } else {
      const jid = await resolveGroupJid(sock, args);
      if (!args.image && !text) fail('Nothing to send (no --message/--message-file and no --image)');
      await sendToJid(sock, jid, text, args.image);
      console.log(`✓ Sent ${args.image ? 'image + caption' : 'message'} to "${jid}"`);
    }
    return 0;
  }
  fail(`Unknown command "${cmd}" (use seed | list-groups | list-members | send | send-batch)`);
}

async function main() {
  const cmd = process.argv[2];
  const args = parseArgs(process.argv.slice(3));

  const { state, saveCreds } = await useMultiFileAuthState(DATA_PATH);

  // Every command except seed needs an already-paired session. After a QR
  // multi-device pairing Baileys leaves creds.registered false and instead
  // populates creds.me / creds.account, so treat either as "paired".
  const paired = !!(state.creds.registered || (state.creds.me && state.creds.account));
  if (cmd !== 'seed' && !paired) {
    fail('Not logged in — run `node send.js seed` and scan the QR first', 3);
  }

  const { version } = await fetchLatestBaileysVersion();

  // Hard timeout so a stuck session never hangs the pipeline. Configurable via
  // WA_SEED_TIMEOUT_MS (default 4 min) — handy to extend for an interactive QR
  // re-seed without weakening the unattended send/send-batch guard.
  const guardMs = parseInt(process.env.WA_SEED_TIMEOUT_MS, 10) || 240000;
  const guard = setTimeout(() => fail('Timed out waiting for WhatsApp', 2), guardMs);

  let ran = false;      // command has been executed (guard against reconnect re-runs)
  let finishing = false;

  async function finish(sock, code) {
    finishing = true;
    clearTimeout(guard);
    // Give WhatsApp a moment to flush the outgoing message before teardown.
    await new Promise((r) => setTimeout(r, 1200));
    try { sock.end(undefined); } catch (e) { /* ignore */ }
    process.exit(code);
  }

  function connect() {
    const sock = makeWASocket({
      version,
      auth: state,
      logger,
      browser: Browsers.macOS('Chrome'),
      markOnlineOnConnect: false,
      syncFullHistory: false,
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (u) => {
      const { connection, lastDisconnect, qr } = u;

      if (qr && cmd === 'seed') showQr(qr);

      if (connection === 'open') {
        if (ran) return;
        ran = true;
        try {
          const code = await runCommand(sock, cmd, args);
          await finish(sock, code);
        } catch (e) {
          clearTimeout(guard);
          fail(e && e.message ? e.message : String(e));
        }
      } else if (connection === 'close') {
        if (finishing) return;
        const code = lastDisconnect && lastDisconnect.error
          && lastDisconnect.error.output && lastDisconnect.error.output.statusCode;
        if (code === DisconnectReason.loggedOut) {
          fail('Session logged out — run `node send.js seed` and re-scan the QR', 3);
        }
        // Any other close (incl. 515 restartRequired right after pairing) is
        // transient: reconnect and keep going until the guard fires.
        if (!ran) connect();
      }
    });
  }

  connect();
}

main().catch((e) => fail(e && e.message ? e.message : String(e)));
