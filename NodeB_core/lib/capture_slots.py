# lib/capture_slots.py — ping-pong capture slots on NodeB.
#
# States per slot: FREE → RECORDING → FULL → SENDING → FREE
# Contract (v2.1 transport):
#   - Full RAW lives ONLY on NodeB (never staged on NodeA).
#   - After RECORDING ends → notify phone (META+END, no DATA) → slot stays FULL.
#   - Phone OP_DUMP → stream only actual_len bytes → FREE slot immediately.
#   - No empty tail of the buffer is transmitted.
#   - TLV_PKT is optional short preview (meta only / ≤16 B), not a second copy of RAW.
#

import time
from protocol import (
    TLV_PKT, TLV_STREAM_META, TLV_STREAM_DATA, TLV_STREAM_END,
    STREAM_DATA_MAX, PREVIEW_MAX, MOD_SUBGHZ,
)

ST_FREE = 0
ST_RECORDING = 1
ST_FULL = 2
ST_SENDING = 3

# Short preview for UI spike only — not full RAW path
_PREVIEW_NOTIFY = 16


class CaptureSlots:
    def __init__(self):
        self.slots = [None, None]
        self.size = 0
        self.state = [ST_FREE, ST_FREE]
        self.meta = [None, None]
        self._rec = -1
        self._send = -1
        self._fifo = []
        self._tlv = bytearray(64)
        self.last_bits = 0
        self.last_len = 0
        self.last_slot = -1
        self.busy = False  # True while STREAM DATA is on the wire

    def attach(self, s0, s1, size):
        self.slots[0] = s0
        self.slots[1] = s1
        self.size = int(size) if size else 0
        self.state[0] = ST_FREE
        self.state[1] = ST_FREE
        self.meta[0] = None
        self.meta[1] = None
        self._rec = -1
        self._send = -1
        self._fifo = []
        self.busy = False

    def clear(self):
        self.slots[0] = None
        self.slots[1] = None
        self.size = 0
        self.state[0] = ST_FREE
        self.state[1] = ST_FREE
        self.meta[0] = None
        self.meta[1] = None
        self._rec = -1
        self._send = -1
        self._fifo = []
        self.last_slot = -1
        self.busy = False

    def _free_slot(self, i):
        if 0 <= i < 2:
            self.state[i] = ST_FREE
            self.meta[i] = None
            if self.last_slot == i:
                self.last_slot = -1

    def begin_record(self):
        """
        Return (slot_index, buf) or (-1, None).
        Ping-pong: prefer FREE slot; never start while STREAM busy.
        Evict only when BOTH slots are FULL — drop oldest (not last_slot first).
        """
        if self._rec >= 0 or self.busy:
            return -1, None
        for i in (0, 1):
            if self.state[i] == ST_FREE and self.slots[i] is not None:
                self.state[i] = ST_RECORDING
                self._rec = i
                return i, self.slots[i]
        # Both occupied — evict the one that is NOT last_slot (keep newest pending)
        victim = -1
        for i in (0, 1):
            if self.state[i] == ST_FULL and self.slots[i] is not None:
                if i != self.last_slot:
                    victim = i
                    break
        if victim < 0:
            # only last_slot left — must drop it to keep listen alive
            victim = self.last_slot if self.last_slot >= 0 else 0
        if 0 <= victim < 2 and self.state[victim] == ST_FULL:
            try:
                while victim in self._fifo:
                    self._fifo.remove(victim)
            except Exception:
                pass
            self._free_slot(victim)
            self.state[victim] = ST_RECORDING
            self._rec = victim
            return victim, self.slots[victim]
        return -1, None

    def finish_record(self, n_bytes, n_bits, mod, freq, rssi, flags=0):
        """Mark current RECORDING slot FULL and enqueue for notify/send."""
        i = self._rec
        if i < 0:
            return -1
        n = int(n_bytes) if n_bytes else 0
        if n < 0:
            n = 0
        if n > self.size:
            n = self.size
        nb = int(n_bits) if n_bits else (n * 8)
        self.meta[i] = (mod & 0xFF, int(freq) & 0xFFFFFFFF, int(rssi), n, nb, flags & 0xFF)
        self.state[i] = ST_FULL
        self._fifo.append(i)
        self._rec = -1
        self.last_len = n
        self.last_bits = nb
        self.last_slot = i
        return i

    def abort_record(self):
        i = self._rec
        if i < 0:
            return
        self.state[i] = ST_FREE
        self.meta[i] = None
        self._rec = -1

    def _feed_wdt(self):
        try:
            import machine
            w = getattr(machine, "WDT", None)
            if w is not None:
                pass  # soft context; kernel feeds in command_loop
        except Exception:
            pass

    def _notify_meta_end(self, link, slot, mod, freq, rssi, n, nb, flags):
        """
        Signal phone that a full frame is ready on NodeB.
        META + END only — zero DATA bytes on UART (no flood during listen).
        NodeA sets capture_ready + capture_seq on STREAM_END.
        """
        t = self._tlv
        t[0] = mod & 0xFF
        t[1] = (freq >> 24) & 255
        t[2] = (freq >> 16) & 255
        t[3] = (freq >> 8) & 255
        t[4] = freq & 255
        t[5] = rssi & 255
        t[6] = (n >> 8) & 255
        t[7] = n & 255
        nbits = nb if nb < 65535 else 65535
        t[8] = (nbits >> 8) & 255
        t[9] = nbits & 255
        t[10] = flags & 255
        t[11] = slot & 255
        link.send_tlv(TLV_STREAM_META, memoryview(t)[:12])
        time.sleep_ms(1)
        t[0] = (n >> 8) & 255
        t[1] = n & 255
        t[2] = (nbits >> 8) & 255
        t[3] = nbits & 255
        t[4] = slot & 255
        link.send_tlv(TLV_STREAM_END, memoryview(t)[:5])

    def _preview_short(self, link, mod, freq, rssi, n, buf):
        """UI spike only: meta + ≤16 B head. Not a second RAW path."""
        prev = n if n <= _PREVIEW_NOTIFY else _PREVIEW_NOTIFY
        if prev < 0:
            prev = 0
        p = self._tlv
        p[0] = mod & 0xFF
        p[1] = (freq >> 24) & 255
        p[2] = (freq >> 16) & 255
        p[3] = (freq >> 8) & 255
        p[4] = freq & 255
        p[5] = rssi & 255
        p[6] = prev & 255
        if prev and buf is not None:
            p[7:7 + prev] = memoryview(buf)[:prev]
        link.send_tlv(TLV_PKT, memoryview(p)[:7 + prev])

    def notify_ready(self, link):
        """
        After RECORDING: tell phone frame is ready. Slot stays FULL until OP_DUMP.
        Returns True if at least one FULL was notified.
        """
        sent = False
        while self._fifo:
            i = self._fifo.pop(0)
            if self.state[i] != ST_FULL or self.slots[i] is None or self.meta[i] is None:
                self.state[i] = ST_FREE
                continue
            mod, freq, rssi, n, nb, flags = self.meta[i]
            if n <= 0:
                self._free_slot(i)
                continue
            try:
                self._notify_meta_end(link, i, mod, freq, rssi, n, nb, flags)
                self._preview_short(link, mod, freq, rssi, n, self.slots[i])
            except Exception:
                pass
            # Stay FULL until OP_DUMP streams and frees
            self.last_slot = i
            sent = True
            self._feed_wdt()
        return sent

    def send_next(self, link, free_after=True):
        """
        Stream one FULL slot (actual_len only). Then FREE if free_after.
        Returns True if a session was sent.
        """
        if self._send >= 0 or self.busy:
            return False
        if not self._fifo:
            # Also allow dump of last FULL not in fifo (after notify)
            i = self.last_slot
            if i < 0 or self.state[i] != ST_FULL:
                return False
        else:
            i = self._fifo.pop(0)
        if self.state[i] != ST_FULL or self.slots[i] is None or self.meta[i] is None:
            self.state[i] = ST_FREE
            return False
        self.state[i] = ST_SENDING
        self._send = i
        self.busy = True
        mod, freq, rssi, n, nb, flags = self.meta[i]
        buf = self.slots[i]
        ok = False
        try:
            self._stream(link, i, mod, freq, rssi, n, nb, flags, buf)
            ok = True
        except Exception:
            pass
        # Clear slot after successful transfer (user contract)
        if free_after and ok:
            self._free_slot(i)
            # Keep last_len/last_bits for local replay until next capture
            self.last_len = n
            self.last_bits = nb
        else:
            self.state[i] = ST_FULL
            self.last_slot = i
        self._send = -1
        self.busy = False
        self._feed_wdt()
        return ok

    def dump_pending(self, link):
        """
        OP_DUMP: stream last FULL (or any FULL slot), then FREE.
        Phone gets complete frame; NodeB buffer cleared.
        """
        if self.busy:
            return False
        i = self.last_slot
        if i < 0 or self.slots[i] is None or self.meta[i] is None:
            # fallback: any FULL
            for j in (0, 1):
                if self.state[j] == ST_FULL and self.meta[j] is not None:
                    i = j
                    break
            else:
                return False
        if self.state[i] not in (ST_FULL, ST_SENDING):
            return False
        # Drop from fifo if present
        try:
            while i in self._fifo:
                self._fifo.remove(i)
        except Exception:
            pass
        self.state[i] = ST_SENDING
        self._send = i
        self.busy = True
        mod, freq, rssi, n, nb, flags = self.meta[i]
        buf = self.slots[i]
        ok = False
        try:
            self._stream(link, i, mod, freq, rssi, n, nb, flags, buf)
            ok = True
        except Exception:
            pass
        if ok:
            self._free_slot(i)
            self.last_len = n
            self.last_bits = nb
        else:
            self.state[i] = ST_FULL
            self.last_slot = i
        self._send = -1
        self.busy = False
        self._feed_wdt()
        return ok

    def flush_all(self, link):
        """Legacy: stream all FULL then free (used by capture_once if needed)."""
        # Prefer notify path; if still FULL in fifo — stream once
        while self.send_next(link, free_after=True):
            pass

    def _stream(self, link, slot, mod, freq, rssi, n, nb, flags, buf):
        """
        Complete-then-chunked. Only actual_len bytes — no empty tail.
        Pacing: give NodeA time to poll HW RX + drain pipe → HTTP.
        At 921600 ~0.7 ms/frame wire; sleep every 4 chunks keeps A pipe ≤32.
        """
        if n <= 0 or buf is None:
            return
        t = self._tlv
        t[0] = mod & 0xFF
        t[1] = (freq >> 24) & 255
        t[2] = (freq >> 16) & 255
        t[3] = (freq >> 8) & 255
        t[4] = freq & 255
        t[5] = rssi & 255
        t[6] = (n >> 8) & 255
        t[7] = n & 255
        nbits = nb if nb < 65535 else 65535
        t[8] = (nbits >> 8) & 255
        t[9] = nbits & 255
        t[10] = flags & 255
        t[11] = slot & 255
        link.send_tlv(TLV_STREAM_META, memoryview(t)[:12])
        time.sleep_ms(3)

        mv = memoryview(buf)
        off = 0
        seq = 0
        # Adaptive pace: larger frames need more headroom for A's HTTP drain
        gap_every = 4
        gap_ms = 2 if n > 2048 else 1
        while off < n:
            take = n - off
            if take > STREAM_DATA_MAX:
                take = STREAM_DATA_MAX
            t[0] = seq & 255
            t[1:1 + take] = mv[off:off + take]
            link.send_tlv(TLV_STREAM_DATA, memoryview(t)[:1 + take])
            off += take
            seq = (seq + 1) & 255
            if (seq % gap_every) == 0:
                time.sleep_ms(gap_ms)
                self._feed_wdt()

        t[0] = (n >> 8) & 255
        t[1] = n & 255
        t[2] = (nbits >> 8) & 255
        t[3] = nbits & 255
        t[4] = slot & 255
        link.send_tlv(TLV_STREAM_END, memoryview(t)[:5])
        time.sleep_ms(2)

    def record_buf(self):
        if self._rec < 0:
            return None
        return self.slots[self._rec]

    def last_buf(self):
        i = self.last_slot
        if i < 0 or self.slots[i] is None:
            return None
        if self.state[i] == ST_FREE:
            return None
        return self.slots[i]

    def has_pending(self):
        """True if any FULL frame waiting for OP_DUMP."""
        for i in (0, 1):
            if self.state[i] == ST_FULL and self.meta[i] is not None:
                return True
        return False
