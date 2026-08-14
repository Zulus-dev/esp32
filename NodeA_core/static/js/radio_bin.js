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
    return { ok: false, error: "bad_magic", link: {}, power: {}, module: {}, telemetry: { events: [], rssi_samples: [] } };
  }
  const online = v[4], boot = v[5], pending = v[6], powered = v[7], rf_on = v[8];
  const err = v[9], mod_id = v[10], ms = v[11], letter = String.fromCharCode(v[12] || 46);
  const event_count = u16be(v, 13);
  const n_ev = v[15];
  const ka_free_kb = u16be(v, 26);
  const ka_age_ms = u16be(v, 28);
  const status_age_ms = u16be(v, 30);
  const capture_seq = u16be(v, 32); // one-shot: changes when NodeB finished a STREAM
  const last_rssi = i8(v[34]);
  const freq_khz = u32be(v, 36);
  const events = [];
  for (let i = 0; i < n_ev; i++) {
    const o = 40 + i * 12;
    if (o + 12 > v.length) break;
    const kind = v[o];
    events.push({ k: EV[kind] || ("?" + kind), a: u32be(v, o + 2), b: i16be(v, o + 6), c: u16be(v, o + 8) });
  }
  // RSSI samples right after events (no last_raw trailer)
  const rssiOff = 40 + n_ev * 12;
  const rssi_samples = [];
  if (v.length > rssiOff) {
    const nrs = v[rssiOff] || 0;
    for (let i = 0; i < nrs; i++) {
      const o = rssiOff + 1 + i * 5;
      if (o + 5 > v.length) break;
      rssi_samples.push({ freq_khz: u32be(v, o), rssi: i8(v[o + 4]) });
    }
  }
  return {
    ok: true,
    link: { online: !!online, pending: !!pending, boot_ok: !!boot, ka_free_kb, ka_age_ms, status_age_ms },
    power: { powered: !!powered, rf_on: !!rf_on, err, rf_letter: letter },
    module: { id: mod_id, name: MOD[mod_id] || "none", state: STATE[ms] || "idle", letter: rf_on ? letter : "." },
    telemetry: {
      last_rssi: last_rssi === -128 ? null : last_rssi,
      freq_khz, event_count, events, rssi_samples, capture_seq
    }
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

/** Sample period used by NodeB CC1101 async capture (µs). */
const SUBGHZ_SAMPLE_US = 25;

/**
 * Bit-packed hex (MSB-first, sample = SUBGHZ_SAMPLE_US) → Flipper-like durations.
 * Returns array of signed µs (+HIGH / -LOW). Empty if no transitions.
 */
function hexToDurations(hex, sampleUs) {
  const bits = hexToBits(hex);
  if (!bits || bits.length < 2) return [];
  const us = sampleUs || SUBGHZ_SAMPLE_US;
  const out = [];
  let lvl = bits[0];
  let run = 1;
  for (let i = 1; i < bits.length; i++) {
    if (bits[i] === lvl) {
      run++;
    } else {
      const d = run * us;
      out.push(lvl === "1" ? d : -d);
      lvl = bits[i];
      run = 1;
    }
  }
  const d = run * us;
  out.push(lvl === "1" ? d : -d);
  // Drop leading silence (Flipper RAW starts with positive/HIGH when possible)
  while (out.length && out[0] < 0) out.shift();
  return out;
}

/** Format durations as Flipper RAW_Data lines (max ~512 values/line). */
function formatRawDataLines(durs, perLine) {
  if (!durs || !durs.length) return [];
  const n = perLine || 512;
  const lines = [];
  for (let i = 0; i < durs.length; i += n) {
    const chunk = durs.slice(i, i + n);
    lines.push(chunk.map(v => (v > 0 ? "+" : "") + v).join(" "));
  }
  return lines;
}

function _allSame(s, ch) {
  if (!s || !s.length) return true;
  for (let i = 0; i < s.length; i++) if (s[i] !== ch) return false;
  return true;
}

/**
 * Phone-side Sub-GHz RAW decoder (Flipper-like).
 * TE reported in microseconds. Rejects all-zero / all-one keys.
 */
function decodeSubghzRaw(hex) {
  const bits = hexToBits(hex);
  const out = {
    proto: "RAW", bits: bits.length, symbols: 0, key: "",
    te: 0, te_us: 0, preview: decodeOokPreview(hex), data: hex || "", note: ""
  };
  if (bits.length < 24) { out.note = "too short"; return out; }

  const runs = [];
  let cur = bits[0], n = 1;
  for (let i = 1; i < bits.length; i++) {
    if (bits[i] === cur) n++;
    else { runs.push({ b: cur, n }); cur = bits[i]; n = 1; }
  }
  runs.push({ b: cur, n });

  // TE ≈ lower-tercile of short high pulses (in samples)
  const pos = runs.filter(r => r.b === "1" && r.n >= 1 && r.n <= 24).map(r => r.n).sort((a, b) => a - b);
  const teSamp = pos.length ? pos[Math.floor(pos.length / 3)] : 1;
  out.te = teSamp;
  out.te_us = teSamp * SUBGHZ_SAMPLE_US;

  // High runs → symbols (Princeton-style: short=0, long=1)
  let symbols = "";
  for (const r of runs) {
    if (r.b !== "1") continue;
    if (r.n < teSamp * 0.45) continue;
    symbols += r.n >= teSamp * 2.0 ? "1" : "0";
  }
  out.symbols = symbols.length;

  function acceptKey(k) {
    if (!k || k.length < 8) return false;
    if (_allSame(k, "0") || _allSame(k, "1")) return false;
    // need some variety
    let z = 0;
    for (let i = 0; i < k.length; i++) if (k[i] === "0") z++;
    return z >= 2 && (k.length - z) >= 2;
  }

  if (symbols.length >= 24 && symbols.length <= 28) {
    const k = symbols.slice(0, 24);
    if (acceptKey(k)) { out.proto = "Princeton"; out.key = k; }
  } else if (symbols.length >= 12 && symbols.length <= 13) {
    const k = symbols.slice(0, 12);
    if (acceptKey(k)) { out.proto = "CAME/12"; out.key = k; }
  } else if (symbols.length >= 18 && symbols.length <= 24) {
    const k = symbols.slice(0, Math.min(24, symbols.length));
    if (acceptKey(k)) {
      out.proto = symbols.length <= 20 ? "Linear/18" : "OOK-fixed";
      out.key = k;
    }
  } else if (symbols.length >= 8 && symbols.length < 12) {
    const k = symbols.slice(0, symbols.length);
    if (acceptKey(k)) { out.proto = "OOK-fixed"; out.key = k; }
  }

  if (out.proto === "RAW" && symbols.length >= 8) {
    out.note = "no_match";
    out.key = "";
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

/**
 * Full capture: NodeB holds RAW; GET asks NodeA to pipe OP_DUMP chunks to this response.
 * Returns { freq_khz, rssi, nbits, len, hex, raw } or null.
 */
async function radioFullCapture() {
  try {
    const r = await fetch("/api/radio/capture");
    if (!r.ok) return null;
    const buf = await r.arrayBuffer();
    if (!buf || buf.byteLength < 9) return null;
    const v = new Uint8Array(buf);
    const freq = u32be(v, 0);
    const rssi = i8(v[4]);
    const nbits = u16be(v, 5);
    const n = u16be(v, 7);
    if (n <= 0 || v.length < 9 + n) return null;
    const raw = v.subarray(9, 9 + n);
    return { freq_khz: freq, rssi, nbits, len: n, hex: toHex(raw, n), raw };
  } catch (e) {
    return null;
  }
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

/* Device-side session backup removed — Save is phone-only download (unified log). */
