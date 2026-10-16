"""Encodeur / décodeur du protocole IR de la télécommande Gree/Lennox YT1FF.

Ce module est du Python pur (aucune dépendance Home Assistant) afin de pouvoir
être testé hors HA (voir tests/ et tools/).

Structure du signal (déduite de 52 captures Pronto, toutes reproduites à l'octet près)
-------------------------------------------------------------------------------------
Une pression de touche = 2 « sous-trames » successives (marqueur 0 puis 1).
Chaque sous-trame fait 8 octets (bits émis LSB en premier) :

    en-tête 9 ms / 4,5 ms
    octets 0..3 (32 bits)
    3 bits de séparation « 0 1 0 »
    marque + silence de 20 ms
    octets 4..7 (32 bits)
    marque finale + silence (40 ms après la trame 0, ~200 ms après la trame 1)

    b0 : bits 0-2 mode (0 auto,1 cool,2 dry,3 fan,4 heat) | bit 3 power |
         bits 4-5 vitesse ventilo (0 auto,1,2,3) | bit 6 « balayage mobile »
    b1 : bits 0-3 température - 16 | bits 4-7 : MINUTERIE (voir plus bas)
    b2 : bits 0-3 : MINUTERIE | bit 4 turbo | bit 5 voyant | bit 6 « health » | bit 7 X-fan
    b3 : 0x50 (sous-trame 0) / 0x60 (sous-trame 1)
    b4 : bits 0-3 position du volet vertical
    b5 : 0x20 | bits 0-1 affichage de température (0 éteint, 1 consigne, 2 intérieur, 3 extérieur)
         (sous-trame 1 : b4 = 0 et b5/b6 = champ « V », voir plus bas)
    b6 : 0x00
    b7 : bits 4-7 somme de contrôle | bits 0-3 numéro de sous-trame (0 ou 1)

    somme = (Σ quartets bas de b0..b3 + Σ quartets hauts de b4..b6 + 10) & 0xF

Minuterie (découverte avec les captures supplémentaires et timer.txt)
---------------------------------------------------------------------
La télécommande peut programmer deux minuteries : « marche » (on) et « arrêt » (off).
  * Aucune minuterie : UNE seule sous-trame (marqueur 0), b1 bits 4-7 = 0 et b2 bits 0-3 = 0.
  * Au moins une minuterie : b1/b2 = temps restant de la minuterie la plus proche, arrondi AU-DESSUS
    à la demi-heure (b1 bit 7 = active, bit 4 = demi-heure, bits 5-6 = dizaines d'heures,
    b2 bits 0-3 = unités d'heures) ET une seconde sous-trame (b3 = 0x60) dont le marqueur (bits 0-3
    de b7) est un masque : 1 = arrêt programmé, 2 = marche programmée, 3 = les deux.
    Les octets b4..b6 de cette sous-trame forment X = b4 | b5 << 8 | b6 << 16 :
        bits 0-10  : minutes restantes avant la MARCHE
        bit 11     : toujours 1
        bits 12-23 : minutes restantes avant l'ARRÊT
    Les minutes décroissent d'une unité par minute réelle.
I feel (captures I_feel.txt)
----------------------------
  * Drapeau « I feel » : bit 2 de b5 (b5 = 0x20 | affichage | 0x04) — confirmé par comparaison de deux
    captures identiques sauf ce bit.
  * Quand I feel est actif, la télécommande envoie après la trame d'état, et aussi seule à intervalles
    réguliers (environ toutes les 5 minutes), une COURTE trame de 16 bits : en-tête 6 ms / 3 ms
    (0x00E6 / 0x0074), deux octets [T, 0xA5] puis une marque finale et ~200 ms de silence.
    T = température ambiante mesurée par la télécommande, en °C entiers. CONFIRMÉ sur l'unité avec des
    trames générées à 18, 21, 24 et 27 °C (l'unité affiche la valeur envoyée) et avec la capture à 25 °C. Le second octet 0xA5 est constant dans toutes les captures.
Les 55 captures (hors I feel) vérifient le reste. Les premières versions de cette intégration envoyaient une
minuterie d'arrêt figée d'environ 17 h : elle est désormais désactivée par défaut.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from enum import IntEnum
import re

CARRIER_HZ = 38_000
PRONTO_FREQ_WORD = 0x006C            # 38,4 kHz, identique aux captures
PRONTO_UNIT_US = PRONTO_FREQ_WORD * 0.241246   # ≈ 26,05 µs par cycle

# Durées en cycles Pronto (valeurs médianes relevées dans les captures)
HDR_MARK, HDR_SPACE = 0x0159, 0x00AD
BIT_MARK = 0x0018
SPACE_0, SPACE_1 = 0x0016, 0x0040
MID_SPACE = 0x0303                   # ~20 ms entre les 2 moitiés
GAP_AFTER_FRAME0 = 0x0606            # ~40 ms
GAP_AFTER_FRAME1 = 0x1E14            # ~200 ms
FOOTER_BITS = (0, 1, 0)


class Mode(IntEnum):
    AUTO = 0
    COOL = 1
    DRY = 2
    FAN = 3
    HEAT = 4


class Fan(IntEnum):
    AUTO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Display(IntEnum):
    OFF = 0
    TARGET = 1
    INSIDE = 2
    OUTSIDE = 3


# Position du volet : nom -> (code 4 bits dans b4, bit « balayage mobile » dans b0)
# Les 10 positions sont confirmées par les captures.
SWING_CODES: dict[str, tuple[int, int]] = {
    "off": (0x0, 0),
    "fixed_up": (0x2, 0),
    "fixed_middle_up": (0x3, 0),
    "fixed_middle": (0x4, 0),
    "fixed_middle_down": (0x5, 0),
    "fixed_down": (0x6, 0),
    "swing_down": (0x7, 1),
    "swing_middle": (0x9, 1),
    "swing_up": (0xB, 1),
    "swing_full": (0x1, 1),
}
SWING_VERIFIED = set(SWING_CODES)  # les 10 positions sont confirmées par captures
SWING_BY_CODE = {v: k for k, v in SWING_CODES.items()}

MIN_TEMP, MAX_TEMP = 16, 30


@dataclass(frozen=True)
class YT1FFState:
    """État complet de la télécommande."""

    power: bool = False
    mode: Mode = Mode.COOL
    temperature: int = 24
    fan: Fan = Fan.AUTO
    swing: str = "off"
    xfan: bool = False
    health: bool = True
    light: bool = True
    display: Display = Display.INSIDE
    turbo: bool = False
    ifeel: bool = False
    timer_on_minutes: int = 0   # minuterie « marche » restante (0 = aucune), max 1440
    timer_off_minutes: int = 0  # minuterie « arrêt » restante (0 = aucune), max 1440

    def with_(self, **kw) -> "YT1FFState":
        return replace(self, **kw)


# --------------------------------------------------------------------------
# Encodage
# --------------------------------------------------------------------------
MAX_TIMER_MIN = 24 * 60


AUTO_TARGET_C = 23     # consigne affichée / retenue en mode auto (l'unité se fixe à 23 °C)
AUTO_WIRE_C = 25       # valeur que la vraie télécommande met dans la trame en mode auto (captures)
XFAN_MODES = (Mode.COOL, Mode.DRY)      # X-Fan forcé à off en auto, ventilo et chauffage
TURBO_MODES = (Mode.COOL, Mode.HEAT)    # turbo forcé à off en auto, déshumidification et ventilo


def forced_params(mode: Mode) -> set[str]:
    """Paramètres imposés par le mode (non modifiables par l'utilisateur)."""
    forced: set[str] = set()
    if mode == Mode.AUTO:
        forced.add("temperature")
    if mode not in XFAN_MODES:
        forced.add("xfan")
    if mode not in TURBO_MODES:
        forced.add("turbo")
    if mode == Mode.DRY:
        forced.add("fan")
    return forced


def normalize(state: YT1FFState) -> YT1FFState:
    """Applique les règles de la vraie télécommande (observées / indiquées par l'utilisateur)."""
    changes: dict = {}
    if state.mode == Mode.AUTO and state.temperature != AUTO_TARGET_C:
        changes["temperature"] = AUTO_TARGET_C
    if state.mode == Mode.DRY and state.fan != Fan.LOW:
        changes["fan"] = Fan.LOW
    if state.mode not in XFAN_MODES and state.xfan:
        changes["xfan"] = False
    if state.mode not in TURBO_MODES and state.turbo:
        changes["turbo"] = False
    on = min(max(int(state.timer_on_minutes), 0), MAX_TIMER_MIN)
    off = min(max(int(state.timer_off_minutes), 0), MAX_TIMER_MIN)
    if (on, off) != (state.timer_on_minutes, state.timer_off_minutes):
        changes.update(timer_on_minutes=on, timer_off_minutes=off)
    return state.with_(**changes) if changes else state


# --- Mémoire des réglages par mode ------------------------------------------
MODE_DEFAULTS: dict[Mode, dict] = {
    Mode.AUTO: {"temperature": AUTO_TARGET_C, "fan": Fan.AUTO, "xfan": False, "swing": "off"},
    Mode.COOL: {"temperature": 24, "fan": Fan.AUTO, "xfan": True, "swing": "off"},
    Mode.DRY: {"temperature": 24, "fan": Fan.LOW, "xfan": True, "swing": "off"},
    Mode.FAN: {"temperature": 24, "fan": Fan.AUTO, "xfan": False, "swing": "off"},
    Mode.HEAT: {"temperature": 21, "fan": Fan.AUTO, "xfan": False, "swing": "off"},
}
REMEMBERED = ("temperature", "fan", "xfan", "swing")


def remember(memory: dict, state: YT1FFState) -> None:
    """Retient consigne, ventilation, X-Fan et volet du mode courant (hors valeurs imposées)."""
    forced = forced_params(state.mode)
    slot = memory.setdefault(state.mode.name.lower(), {})
    for key in REMEMBERED:
        if key not in forced:
            value = getattr(state, key)
            slot[key] = int(value) if key == "fan" else value


def recall(memory: dict, mode: Mode) -> dict:
    """Réglages à appliquer en passant à ``mode`` : mémoire, sinon valeurs par défaut."""
    out = dict(MODE_DEFAULTS[mode])
    saved = memory.get(mode.name.lower(), {})
    for key in REMEMBERED:
        if key in saved:
            out[key] = Fan(saved[key]) if key == "fan" else saved[key]
    if saved.get("swing") not in SWING_CODES:
        out["swing"] = MODE_DEFAULTS[mode]["swing"]
    return out


def _checksum(b: list[int]) -> int:
    s = sum(x & 0xF for x in b[0:4]) + sum((x >> 4) & 0xF for x in b[4:7]) + 10
    return s & 0xF


def _timer_nibbles(minutes: int) -> tuple[int, int]:
    """(quartet haut de b1, quartet bas de b2) pour une minuterie en minutes."""
    if minutes <= 0:
        return 0, 0
    n = math.ceil(minutes / 30)          # demi-heures, arrondi au-dessus
    hours, half = divmod(n, 2)
    return 0x8 | half | ((hours // 10) << 1), hours % 10


def _soonest(state: YT1FFState) -> int:
    active = [m for m in (state.timer_on_minutes, state.timer_off_minutes) if m > 0]
    return min(active) if active else 0


def frame0_bytes(state: YT1FFState) -> list[int]:
    state = normalize(state)
    if not MIN_TEMP <= state.temperature <= MAX_TEMP:
        raise ValueError(f"température hors plage {MIN_TEMP}-{MAX_TEMP}")
    code, flag = SWING_CODES[state.swing]
    t_hi, t_lo = _timer_nibbles(_soonest(state))
    b = [0] * 8
    b[0] = (int(state.mode) & 7) | (int(state.power) << 3) | (int(state.fan) << 4) | (flag << 6)
    wire_temp = AUTO_WIRE_C if state.mode == Mode.AUTO else state.temperature
    b[1] = (t_hi << 4) | (wire_temp - 16)
    b[2] = t_lo | (int(state.turbo) << 4) | (int(state.light) << 5) \
        | (int(state.health) << 6) | (int(state.xfan) << 7)
    b[3] = 0x50
    b[4] = code
    b[5] = 0x20 | int(state.display) | (int(state.ifeel) << 2)
    b[6] = 0x00
    b[7] = _checksum(b) << 4
    return b


def frame1_bytes(state: YT1FFState) -> list[int] | None:
    """Sous-trame des minuteries ; None s'il n'y a aucune minuterie (trame unique)."""
    state = normalize(state)
    on, off = state.timer_on_minutes, state.timer_off_minutes
    if on <= 0 and off <= 0:
        return None
    f0 = frame0_bytes(state)
    x = (on & 0x7FF) | 0x800 | (off << 12)
    marker = (1 if off > 0 else 0) | (2 if on > 0 else 0)
    b = [f0[0], f0[1], f0[2], 0x60, x & 0xFF, (x >> 8) & 0xFF, (x >> 16) & 0xFF, 0]
    b[7] = (_checksum(b) << 4) | marker
    return b


# --------------------------------------------------------------------------
# Timings / Pronto
# --------------------------------------------------------------------------
def _bits_lsb(byte: int) -> list[int]:
    return [(byte >> i) & 1 for i in range(8)]


def _frame_pairs(b: list[int], gap: int) -> list[tuple[int, int]]:
    pairs = [(HDR_MARK, HDR_SPACE)]
    for byte in b[:4]:
        pairs += [(BIT_MARK, SPACE_1 if bit else SPACE_0) for bit in _bits_lsb(byte)]
    pairs += [(BIT_MARK, SPACE_1 if bit else SPACE_0) for bit in FOOTER_BITS]
    pairs.append((BIT_MARK, MID_SPACE))
    for byte in b[4:]:
        pairs += [(BIT_MARK, SPACE_1 if bit else SPACE_0) for bit in _bits_lsb(byte)]
    pairs.append((BIT_MARK, gap))
    return pairs


IFEEL_HDR_MARK, IFEEL_HDR_SPACE = 0x00E6, 0x0074
GAP_AFTER_FRAME0_IFEEL = 0x1DFF           # ~200 ms entre la trame d'état et la trame I feel
IFEEL_SIGNATURE = 0xA5
IFEEL_MIN_C, IFEEL_MAX_C = 8.0, 40.0


def ifeel_bytes(temp_c: float) -> list[int]:
    """Deux octets de la trame I feel : température ambiante en °C entiers (0x19 -> 25 °C affiché)."""
    if not IFEEL_MIN_C <= temp_c <= IFEEL_MAX_C:
        raise ValueError(f"température ambiante hors plage {IFEEL_MIN_C}-{IFEEL_MAX_C}")
    return [int(math.floor(temp_c + 0.5)), IFEEL_SIGNATURE]


def ifeel_temperature(b: list[int]) -> float:
    return float(b[0])


def _ifeel_pairs(temp_c: float, gap: int) -> list[tuple[int, int]]:
    pairs = [(IFEEL_HDR_MARK, IFEEL_HDR_SPACE)]
    for byte in ifeel_bytes(temp_c):
        pairs += [(BIT_MARK, SPACE_1 if bit else SPACE_0) for bit in _bits_lsb(byte)]
    pairs.append((BIT_MARK, gap))
    return pairs


def build_ifeel_pairs(temp_c: float) -> list[tuple[int, int]]:
    """Trame I feel seule (envoi périodique)."""
    return _ifeel_pairs(temp_c, GAP_AFTER_FRAME1)


def build_pairs(state: YT1FFState, ambient: float | None = None) -> list[tuple[int, int]]:
    """Séquence (marque, silence) en cycles Pronto.

    1 trame sans minuterie, 2 avec. Si ``state.ifeel`` et ``ambient`` fournie : la trame d'état est
    suivie de la trame I feel, comme sur la vraie télécommande.
    """
    f0 = frame0_bytes(state)
    f1 = frame1_bytes(state)
    if f1 is None:
        if state.ifeel and ambient is not None:
            return _frame_pairs(f0, GAP_AFTER_FRAME0_IFEEL) + _ifeel_pairs(ambient, GAP_AFTER_FRAME1)
        return _frame_pairs(f0, GAP_AFTER_FRAME1)
    return _frame_pairs(f0, GAP_AFTER_FRAME0) + _frame_pairs(f1, GAP_AFTER_FRAME1)


def pairs_to_pronto(pairs: list[tuple[int, int]]) -> str:
    words = [0x0000, PRONTO_FREQ_WORD, len(pairs), 0x0000]
    for m, s in pairs:
        words += [m, s]
    return " ".join(f"{w:04X}" for w in words)


def pairs_to_us(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    return [(round(m * PRONTO_UNIT_US), round(s * PRONTO_UNIT_US)) for m, s in pairs]


def state_to_pronto(state: YT1FFState, ambient: float | None = None) -> str:
    return pairs_to_pronto(build_pairs(state, ambient))


# --------------------------------------------------------------------------
# Décodage (outil de diagnostic / validation)
# --------------------------------------------------------------------------
def parse_pronto(text: str) -> list[tuple[int, int]]:
    words = [int(w, 16) for w in re.findall(r"[0-9A-Fa-f]{4}", text.split("\n")[0])]
    if words[0] != 0 or len(words) < 6:
        raise ValueError("Pronto « learned » (0000) attendu")
    n1 = words[2]
    body = words[4:4 + 2 * n1]
    return [(body[i], body[i + 1]) for i in range(0, len(body), 2)]


def _split_frames(pairs: list[tuple[int, int]]) -> list[list[int]]:
    """Découpe en trames (liste de bits ; -1 = long silence) à chaque en-tête (marque ≥ 0xC0)."""
    frames: list[list[int]] = []
    cur: list[int] | None = None
    for m, s in pairs:
        if m >= 0xC0:
            cur = []
            frames.append(cur)
            continue
        cur.append(0 if s < 0x30 else 1 if s < 0x60 else -1)
    return frames


def _bits_to_bytes(bits: list[int]) -> list[int]:
    return [sum(bit << k for k, bit in enumerate(bits[i:i + 8])) for i in range(0, len(bits), 8)]


def decode_frames(pairs: list[tuple[int, int]]) -> list[list[int]]:
    """Sous-trames de 8 octets (les trames I feel de 16 bits sont ignorées, voir decode_ifeel)."""
    out = []
    for bits in _split_frames(pairs):
        if len(bits) == 17 and bits[-1] == -1:      # trame courte I feel
            continue
        first, footer, second = bits[:32], bits[32:35], bits[36:68]
        if len(bits) < 69 or footer != list(FOOTER_BITS) or -1 in first + second:
            raise ValueError("sous-trame invalide")
        out.append(_bits_to_bytes(first) + _bits_to_bytes(second))
    return out


def decode_ifeel(pairs: list[tuple[int, int]]) -> list[int] | None:
    """Deux octets de la trame I feel, ou None s'il n'y en a pas."""
    for bits in _split_frames(pairs):
        if len(bits) == 17 and bits[-1] == -1 and -1 not in bits[:16]:
            return _bits_to_bytes(bits[:16])
    return None


def decode_state(frame0: list[int], frame1: list[int] | None = None) -> YT1FFState:
    b = frame0
    on = off = 0
    if frame1 is not None and (b[1] >> 7):
        x = frame1[4] | (frame1[5] << 8) | (frame1[6] << 16)
        marker = frame1[7] & 0xF
        if marker & 2:
            on = x & 0x7FF
        if marker & 1:
            off = x >> 12
    swing = SWING_BY_CODE.get((b[4] & 0xF, (b[0] >> 6) & 1), "off")
    return YT1FFState(
        power=bool(b[0] & 8),
        mode=Mode(b[0] & 7),
        temperature=AUTO_TARGET_C if (b[0] & 7) == Mode.AUTO else (b[1] & 0xF) + 16,
        fan=Fan((b[0] >> 4) & 3),
        swing=swing,
        xfan=bool(b[2] & 0x80),
        health=bool(b[2] & 0x40),
        light=bool(b[2] & 0x20),
        display=Display(b[5] & 3),
        ifeel=bool(b[5] & 4),
        turbo=bool(b[2] & 0x10),
        timer_on_minutes=on,
        timer_off_minutes=off,
    )


def checksum_ok(frame: list[int]) -> bool:
    return (frame[7] >> 4) == _checksum(frame)


# --------------------------------------------------------------------------
# Diagnostic
# --------------------------------------------------------------------------
def pronto_frequency_word(text: str) -> int:
    return int(re.findall(r"[0-9A-Fa-f]{4}", text)[1], 16)


def describe_pronto(text: str) -> dict:
    """Décode un code Pronto et dit si le générateur le reproduit à l'identique."""
    pairs = parse_pronto(text)
    frames = decode_frames(pairs)
    ifeel = decode_ifeel(pairs)
    if not frames:
        return {"ifeel_frame_hex": " ".join(f"{b:02X}" for b in ifeel) if ifeel else None,
                "ambient_temperature": ifeel_temperature(ifeel) if ifeel else None}
    state = decode_state(frames[0], frames[1] if len(frames) > 1 else None)
    f1 = frame1_bytes(state)
    same = frame0_bytes(state) == frames[0] and (
        (f1 is None and len(frames) == 1) or f1 == frames[1])
    return {
        "frames_hex": [" ".join(f"{b:02X}" for b in f) for f in frames],
        "checksums_ok": all(checksum_ok(f) for f in frames),
        "state": {
            "power": state.power, "mode": state.mode.name.lower(), "temperature": state.temperature,
            "fan": state.fan.name.lower(), "swing": state.swing, "xfan": state.xfan,
            "health": state.health, "light": state.light, "display": state.display.name.lower(),
            "turbo": state.turbo, "timer_on_minutes": state.timer_on_minutes,
            "timer_off_minutes": state.timer_off_minutes, "ifeel": state.ifeel,
        },
        "ifeel_frame_hex": " ".join(f"{b:02X}" for b in ifeel) if ifeel else None,
        "ambient_temperature": ifeel_temperature(ifeel) if ifeel else None,
        "regenerated_identical": same,
    }
