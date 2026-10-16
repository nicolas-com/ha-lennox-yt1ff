#!/usr/bin/env python3
"""Décode des codes Pronto de la YT1FF et compare avec le générateur.

Usage :
    python tools/analyze_pronto.py capture.txt              # lignes commençant par « 0000 006C »
    python tools/analyze_pronto.py --gen cool 20 low        # génère un code (mode, température, ventilo)
    python tools/analyze_pronto.py --gen cool 20 low 90 0   # idem avec minuterie de marche 90 min, arrêt 0
"""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location(
    "protocol", Path(__file__).parent.parent / "custom_components/lennox_yt1ff/protocol.py")
P = importlib.util.module_from_spec(spec)
sys.modules["protocol"] = P
spec.loader.exec_module(P)


def analyse(code: str, label: str) -> None:
    f = P.decode_frames(P.parse_pronto(code))
    st = P.decode_state(f[0], f[1] if len(f) > 1 else None)
    f1 = P.frame1_bytes(st)
    same = P.frame0_bytes(st) == f[0] and ((f1 is None and len(f) == 1) or f1 == f[1])
    second = ' '.join(f'{b:02X}' for b in f[1]) if len(f) > 1 else '(trame unique)'
    print(f"{label:>6}  {' '.join(f'{b:02X}' for b in f[0])} | {second}  "
          f"{'OK ' if same else 'DIFFÉRENT'} {st}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--gen":
        mode, temp, fan = sys.argv[2], int(sys.argv[3]), sys.argv[4]
        t_on = int(sys.argv[5]) if len(sys.argv) > 5 else 0
        t_off = int(sys.argv[6]) if len(sys.argv) > 6 else 0
        st = P.YT1FFState(power=True, mode=P.Mode[mode.upper()], temperature=temp, fan=P.Fan[fan.upper()],
                          xfan=mode in ("cool", "dry"), timer_on_minutes=t_on, timer_off_minutes=t_off)
        print(P.state_to_pronto(st))
    else:
        n = 0
        for line in Path(sys.argv[1]).read_text().split("\n"):
            if line.startswith("0000 006C"):
                n += 1
                analyse(line, str(n))
