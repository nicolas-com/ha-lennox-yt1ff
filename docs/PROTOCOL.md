# YT1FF infrared protocol (as observed)

Everything below was deduced from recorded Pronto captures of one YT1FF remote (see `tests/`, 61 captures) and checked by
`python tests/test_protocol.py`, which regenerates every capture byte-for-byte. It is **observation, not a specification**:
other units may differ.

## Physical layer

Carrier 38.4 kHz (Pronto frequency word `0x006C`). Bits are sent LSB first.

| Element | Mark / space |
|---|---|
| Header | 9 ms / 4.5 ms |
| Bit | mark ~0.6 ms; space ~0.57 ms (0) or ~1.67 ms (1) |
| Mid-frame connector | after 32 bits + 3 bits `0 1 0`: mark + ~20 ms space |
| End | mark + ~40 ms (more frames follow) or ~200 ms (last frame) |

A *state frame* is: header, 32 bits (b0–b3), `0 1 0`, connector, 32 bits (b4–b7), end mark.
The *I feel frame* is short (see below). A key press sends one state frame; with timers active it sends a second one.

## State frame (8 bytes)

| Byte | Content |
|---|---|
| b0 | bits 0–2 mode (0 auto, 1 cool, 2 dry, 3 fan, 4 heat) · bit 3 power · bits 4–5 fan (0 auto, 1 low, 2 medium, 3 high) · bit 6 swing flag |
| b1 | bits 0–3 temperature − 16 · bits 4–7 timer (see below) |
| b2 | bits 0–3 timer · bit 4 turbo · bit 5 light · bit 6 health · bit 7 X-Fan |
| b3 | `0x50` (state frame) / `0x60` (timer frame) |
| b4 | louver: 0 off, 1 swing full, 2–6 fixed up → down, 7 swing down, 9 swing middle, 0xB swing up |
| b5 | `0x20` · bits 0–1 displayed temperature (0 off, 1 target, 2 inside, 3 outside) · bit 2 **I feel** |
| b6 | `0x00` |
| b7 | bits 4–7 checksum · bits 0–3 frame marker |

`checksum = (Σ low nibbles of b0..b3 + Σ high nibbles of b4..b6 + 10) & 0xF`

Rules seen on the real remote: in **auto** the frame carries 25 °C while the unit settles at 23 °C; X-Fan only in cool/dry; turbo
only in cool/heat; fan forced low in dry.

## I feel

Bit 2 of b5 enables it. The remote then also sends, right after the state frame and alone about every 5 minutes, a **16-bit frame**:
header 6 ms / 3 ms (`0x00E6` / `0x0074`), bytes `[T, 0xA5]`, end mark + ~200 ms. `T` is the remote's measured temperature in whole °C
(confirmed on the unit: frames generated for 18, 21, 24 and 27 °C are displayed as such).

## Timers (decoded, **not used** by the integration)

* No timer: a single state frame, b1 bits 4–7 = 0, b2 bits 0–3 = 0.
* Timer active: b1/b2 hold the time left until the nearest timer, rounded **up** to the half hour (b1 bit 7 active, bit 4 half hour,
  bits 5–6 tens of hours, b2 bits 0–3 units of hours), and a second frame (b3 = `0x60`) is sent. Its marker (b7 bits 0–3) is a mask:
  1 = off timer, 2 = on timer, 3 = both. With `X = b4 | b5 << 8 | b6 << 16`: bits 0–10 = minutes before "on", bit 11 = 1,
  bits 12–23 = minutes before "off". Minutes count down in real time.
* Frames built this way match the remote's captures exactly, but the heat pump did **not** execute timers sent from Home Assistant
  (the remote's own timers work). The cause is unknown.
