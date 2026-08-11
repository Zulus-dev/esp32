// radio_bin.js — binary radio + phone-side Sub-GHz decoders
const WEB_MAGIC = 0xC01B;
const MOD = { 0: "none", 1: "subghz", 0x20: "nrf", 0x30: "wifi", 0x40: "ble" };
const STATE = ["idle", "armed", "on", "scan", "sniff"];
const EV = { 1: "rssi", 2: "pkt", 3: "st", 4: "nrf", 5: "wifi", 6: "ble", 7: "scan" };
const WA = {
  NODE_B_ON: 0x01, NODE_B_OFF: 0x02, MOD_ON: 0x03, MOD_OFF: 0x04,
  SCAN: 0x05, LISTEN: 0x06, CAPTURE: 0x07, RSSI_ONCE: 0x08, REPLAY: 0x09,
  STOP: 0x0A, HID_SNIFF: 0x0B, HONEYPOT: 0x0C, WIFI_SCAN: 0x0D, BLE_SCAN: 0x0E,
  LOG_SAVE: 0x0F, LOG_CLEAR: 0x10, TX_RAW: 0x11
};
const MOD_ID = { subghz: 1, nrf: 0x20, wifi: 0x30, ble: 0x40 };
const RAW_OFF = 40 + 8 * 12;

function u16be(v, o) { return (v[o] << 8) | v[o + 1]; }
function u32be(v, o) { return ((v[o] << 24) | (v[o + 1] << 16) | (v[o + 2] << 8) | v[o + 3]) >>> 0; }
function i8(v) { return v > 127 ? v - 256 : v; }
function i16be(v, o) { let x = u16be(v, o); return x > 32767 ? x - 65536 : x; }
function toHex(u8, n) {
  let s = "";
  for (let i = 0; i < n; i++) s += ("0" + u8[i].toString(16)).slice(-2);
  return s;
}
function hexToBytes(hex) {
  const out = [];
  for (let i = 0; i + 1 < hex.length; i += 2) {
    const b = parseInt(hex.substr(i, 2), 16);
    if (isNaN(b)) break;
    out.push(b);
  }
  return new Uint8Array(out);
}

function decodeStatus(buf) {
  const v = new Uint8Array(buf);
  if (v.length < 40 || u16be(v, 0) !== WEB_MAGIC) {
    return { ok: false, error: "bad_magic", link: {}, power: {}, module: {}, telemetry: { events: [] }, last_raw: null };
  }
  const online = v[4], boot = v[5], pending = v[6], powered = v[7], rf_on = v[8];
  const err = v[9], mod_id = v[10], ms = v[11], letter = String.fromCharCode(v[12] || 46);
  const event_count = u16be(v, 13);
  const n_ev = v[15];
  const rx_frames = u32be(v, 16);
  const crc_fail = u16be(v, 20);
  const raw_rx = u32be(v, 22);
  const ka_free_kb = u16be(v, 26);
  const ka_age_ms = u16be(v, 28);
  const status_age_ms = u16be(v, 30);
  const ka_state = v[32], ka_flags = v[33];
  const last_rssi = i8(v[34]);
  const freq_khz = u32be(v, 36);
  const events = [];
  for (let i = 0; i < n_ev; i++) {
    const o = 40 + i * 12;
    if (o + 12 > v.length) break;
    const kind = v[o];
    events.push({ k: EV[kind] || ("?" + kind), a: u32be(v, o + 2), b: i16be(v, o + 6), c: u16be(v, o + 8) });
  }
  let last_raw = null;
  let rawEnd = RAW_OFF;
  if (v.length >= RAW_OFF + 6) {
    const plen = v[RAW_OFF];
    if (plen > 0 && plen <= 48 && v.length >= RAW_OFF + 6 + plen) {
      last_raw = {
        freq_khz: u32be(v, RAW_OFF + 2),
        rssi: i8(v[RAW_OFF + 1]),
        len: plen,
        hex: toHex(v.subarray(RAW_OFF + 6, RAW_OFF + 6 + plen), plen)
      };
      rawEnd = RAW_OFF + 6 + plen;
    } else {
      rawEnd = RAW_OFF + 6;
    }
  }
  // RSSI sample batch for waterfall: count_u8 + n × (freq_u32be + rssi_i8)
  const rssi_samples = [];
  if (v.length > rawEnd) {
    const nrs = v[rawEnd] || 0;
    for (let i = 0; i < nrs; i++) {
      const o = rawEnd + 1 + i * 5;
      if (o + 5 > v.length) break;
      rssi_samples.push({ freq_khz: u32be(v, o), rssi: i8(v[o + 4]) });
    }
  }
  return {
    ok: true,
    link: { online: !!online, pending: !!pending, boot_ok: !!boot, rx_frames, crc_fail, raw_rx, ka_free_kb, ka_age_ms, status_age_ms },
    power: { powered: !!powered, rf_on: !!rf_on, err, rf_letter: letter },
    module: { id: mod_id, name: MOD[mod_id] || "none", state: STATE[ms] || "idle", letter: rf_on ? letter : "." },
    telemetry: { last_rssi: last_rssi === -128 ? null : last_rssi, freq_khz, event_count, events, ka_state, ka_flags, rssi_samples },
    last_raw
  };
}

function encodeCmd(action, module, freq_khz, preset, thresh, rawBytes) {
  const head = 8;
  const rn = rawBytes && rawBytes.length ? Math.min(rawBytes.length, 48) : 0;
  const u = new Uint8Array(head + rn);
  u[0] = typeof action === "number" ? action : 0;
  u[1] = MOD_ID[module] || 1;
  const fk = (freq_khz >>> 0);
  u[2] = (fk >> 24) & 255; u[3] = (fk >> 16) & 255; u[4] = (fk >> 8) & 255; u[5] = fk & 255;
  u[6] = (preset || 0) & 255;
  u[7] = (thresh || 72) & 127;
  if (rn) u.set(rawBytes.subarray(0, rn), head);
  return u;
}

function hexToBits(hex) {
  if (!hex) return "";
  let bits = "";
  for (let i = 0; i < hex.length; i += 2) {
    const by = parseInt(hex.substr(i, 2), 16);
    if (isNaN(by)) break;
    for (let j = 7; j >= 0; j--) bits += (by >> j) & 1 ? "1" : "0";
  }
  return bits;
}

function decodeOokPreview(hex) {
  return hexToBits(hex).replace(/0{8,}/g, "0…").replace(/1{8,}/g, "1…").slice(0, 96);
}

/**
 * Phone-side Sub-GHz RAW decoder (Flipper-like labels).
 * Best-effort: Princeton/CAME-like fixed codes from OOK bit runs.
 */
function decodeSubghzRaw(hex) {
  const bits = hexToBits(hex);
  const out = { proto: "RAW", bits: bits.length, symbols: 0, key: "", te: 0, preview: decodeOokPreview(hex), data: hex || "" };
  if (bits.length < 24) { out.note = "too short"; return out; }

  const runs = [];
  let cur = bits[0], n = 1;
  for (let i = 1; i < bits.length; i++) {
    if (bits[i] === cur) n++;
    else { runs.push({ b: cur, n }); cur = bits[i]; n = 1; }
  }
  runs.push({ b: cur, n });

  // TE ≈ median of short positive pulses
  const pos = runs.filter(r => r.b === "1" && r.n >= 1 && r.n <= 20).map(r => r.n).sort((a, b) => a - b);
  const te = pos.length ? pos[Math.floor(pos.length / 3)] : 1;
  out.te = te;

  // Map high runs to 0/1 by duration relative to TE (Princeton: short=0 long=1)
  let symbols = "";
  for (const r of runs) {
    if (r.b !== "1") continue;
    if (r.n < te * 0.5) continue;
    symbols += r.n >= te * 2.2 ? "1" : "0";
  }
  out.symbols = symbols.length;

  if (symbols.length >= 24 && symbols.length <= 28) {
    out.proto = "Princeton";
    out.key = symbols.slice(0, 24);
  } else if (symbols.length >= 12 && symbols.length <= 13) {
    out.proto = "CAME/12";
    out.key = symbols.slice(0, 12);
  } else if (symbols.length >= 18 && symbols.length <= 20) {
    out.proto = "Linear/18";
    out.key = symbols.slice(0, 18);
  } else if (symbols.length >= 8) {
    out.proto = "OOK-fixed";
    out.key = symbols.slice(0, Math.min(32, symbols.length));
  }
  return out;
}

function decodeWifiScanHex(hex) {
  if (!hex || hex.length < 12) return null;
  const b = [];
  for (let i = 0; i < hex.length; i += 2) b.push(parseInt(hex.substr(i, 2), 16));
  const ch = b[2], rssi = i8(b[3]), auth = b[4], n = b[5];
  let ssid = "";
  for (let i = 0; i < n && 6 + i < b.length; i++) ssid += String.fromCharCode(b[6 + i]);
  return { ch, rssi, auth, ssid };
}

function formatEvent(e) {
  if (e.k === "pkt") return "RAW " + (e.a / 1000).toFixed(3) + " MHz  " + e.b + " dBm  " + e.c + " B";
  if (e.k === "st") return "ST " + e.a + "/" + e.b;
  if (e.k === "scan" || e.k === "wifi") {
    const w = decodeWifiScanHex(e.hex);
    if (w) return "AP ch" + w.ch + " " + w.rssi + "dBm \"" + w.ssid + "\"";
    return e.k + " " + e.a + " " + e.b;
  }
  if (e.k === "nrf") return "nRF ev=" + e.a + " ch=" + e.b;
  if (e.k === "ble") return "BLE rssi=" + e.c;
  return e.k + " " + e.a + " " + e.b;
}

async function radioStatus() {
  const r = await fetch("/api/radio/status");
  return decodeStatus(await r.arrayBuffer());
}

async function radioCmd(action, module, extra) {
  const fk = (extra && extra.freq_khz) || 433920;
  const preset = (extra && extra.preset) || 0;
  const thresh = (extra && extra.thresh) || 72;
  let rawBytes = null;
  if (extra && extra.hex) rawBytes = hexToBytes(extra.hex);
  else if (extra && extra.raw) rawBytes = extra.raw;
  const body = encodeCmd(action, module || "subghz", fk, preset, thresh, rawBytes);
  const r = await fetch("/api/radio/cmd", {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body
  });
  return decodeStatus(await r.arrayBuffer());
}

async function radioLogSave() {
  const r = await fetch("/api/radio/log", {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: new Uint8Array([0])
  });
  const buf = new Uint8Array(await r.arrayBuffer());
  if (buf[0] === 1) return { ok: true, path: new TextDecoder().decode(buf.slice(1)) };
  return { ok: false };
}

async function radioLogClear() {
  await fetch("/api/radio/log", {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: new Uint8Array([1])
  });
}
