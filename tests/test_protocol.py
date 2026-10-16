"""Vérifie que le générateur reproduit les 55 captures de la YT1FF.

Lancer :  python tests/test_protocol.py     (ou)   python -m pytest tests -q
"""
from pathlib import Path
import importlib.util
import math
import sys

spec = importlib.util.spec_from_file_location(
    "protocol", Path(__file__).parent.parent / "custom_components/lennox_yt1ff/protocol.py")
P = importlib.util.module_from_spec(spec)
sys.modules["protocol"] = P
spec.loader.exec_module(P)

HERE = Path(__file__).parent


def load():
    codes = []
    for name in ("YT1FF.txt", "captures_sup.txt", "timer.txt"):
        codes += [l for l in (HERE / name).read_text().split("\n") if l.startswith("0000 006C")]
    assert len(codes) == 55, len(codes)
    return codes


def decode(code):
    frames = P.decode_frames(P.parse_pronto(code))
    state = P.decode_state(frames[0], frames[1] if len(frames) > 1 else None)
    return frames, state


def test_all_captures_regenerated_exactly():
    for code in load():
        frames, st = decode(code)
        assert all(P.checksum_ok(f) for f in frames)
        assert P.normalize(st) == st            # la vraie télécommande suit nos règles
        assert P.frame0_bytes(st) == frames[0], (st, frames[0])
        f1 = P.frame1_bytes(st)
        assert (f1 is None and len(frames) == 1) or (f1 == frames[1]), (st, frames)
        assert P.decode_frames(P.parse_pronto(P.state_to_pronto(st))) == frames


def test_timer_fields_match_captures():
    for code in load():
        frames, st = decode(code)
        soonest = P._soonest(st)
        b1, b2 = frames[0][1], frames[0][2]
        half_hours = 2 * (((b1 >> 5) & 3) * 10 + (b2 & 15)) + ((b1 >> 4) & 1)
        assert (b1 >> 7) == (1 if soonest else 0)
        assert half_hours == math.ceil(soonest / 30)


def test_timer_txt_values():
    c = load()[-3:]
    assert decode(c[0])[1].timer_on_minutes == 0 and len(decode(c[0])[0]) == 1      # sans minuterie : 1 trame
    st = decode(c[1])[1]; assert (st.timer_on_minutes, st.timer_off_minutes) == (925, 0)   # 16h35 -> 8h00
    st = decode(c[2])[1]; assert (st.timer_on_minutes, st.timer_off_minutes) == (924, 24)  # + arrêt à 17h00


def test_no_timer_means_single_frame():
    assert len(P.decode_frames(P.parse_pronto(P.state_to_pronto(P.YT1FFState(power=True))))) == 1


def test_all_states_valid_and_roundtrip():
    for mode in P.Mode:
        for t in range(16, 31):
            for fan in P.Fan:
                for sw in P.SWING_CODES:
                    for disp in P.Display:
                        for on, off in ((0, 0), (30, 0), (0, 95), (925, 24), (1440, 1440)):
                            st = P.normalize(P.YT1FFState(power=True, mode=mode, temperature=t, fan=fan,
                                                         swing=sw, display=disp, turbo=True,
                                                         timer_on_minutes=on, timer_off_minutes=off))
                            fr = P.decode_frames(P.parse_pronto(P.state_to_pronto(st)))
                            assert all(P.checksum_ok(x) for x in fr)
                            assert P.decode_state(fr[0], fr[1] if len(fr) > 1 else None) == st


def test_ifeel_captures():
    codes = [l for l in (HERE / "I_feel.txt").read_text().split("\n") if l.startswith("0000 006")]
    assert len(codes) == 6
    expected = [  # (trame d'état ?, ifeel actif, température ambiante lue)
        (True, True, 22.0), (False, None, 24.0), (False, None, 24.0), (True, True, 24.0), (True, False, None),
        (False, None, 25.0)]
    for code, (has_state, flag, ambient) in zip(codes, expected):
        pairs = P.parse_pronto(code)
        frames, short = P.decode_frames(pairs), P.decode_ifeel(pairs)
        assert bool(frames) == has_state
        assert (short is None) == (ambient is None)
        if short:
            assert short[1] == 0xA5 and P.ifeel_temperature(short) == ambient
            assert P.ifeel_bytes(ambient) == short
        if frames:
            st = P.decode_state(frames[0])
            assert st.ifeel == flag and P.normalize(st) == st
            again = P.build_pairs(st, ambient)
            assert P.decode_frames(again) == frames and P.decode_ifeel(again) == short
        else:
            assert P.decode_ifeel(P.build_ifeel_pairs(ambient)) == short


def test_forced_rules_per_mode():
    for mode in P.Mode:
        st = P.normalize(P.YT1FFState(power=True, mode=mode, temperature=27, fan=P.Fan.HIGH,
                                     xfan=True, turbo=True))
        assert st.xfan == (mode in (P.Mode.COOL, P.Mode.DRY))
        assert st.turbo == (mode in (P.Mode.COOL, P.Mode.HEAT))
        assert (st.fan == P.Fan.LOW) == (mode == P.Mode.DRY)
        assert (st.temperature == 23) == (mode == P.Mode.AUTO)


def test_auto_mode_wire_temperature_is_25_but_state_is_23():
    st = P.normalize(P.YT1FFState(power=True, mode=P.Mode.AUTO, temperature=18))
    assert st.temperature == 23
    assert P.frame0_bytes(st)[1] & 0xF == 9                   # 25 °C sur le fil, comme la vraie télécommande
    assert P.decode_state(P.frame0_bytes(st)).temperature == 23


def test_per_mode_memory():
    import json
    mem, state = {}, P.normalize(P.YT1FFState(power=True, mode=P.Mode.COOL))

    def change(state, **ch):                                   # même logique que controller.async_update
        if (m := ch.get("mode")) is not None and m != state.mode:
            state = state.with_(**P.recall(mem, m))
        state = P.normalize(state.with_(**ch)); P.remember(mem, state); return state

    state = change(state, temperature=22, fan=P.Fan.HIGH, swing="fixed_up", xfan=False)
    state = change(state, mode=P.Mode.HEAT)
    assert (state.temperature, state.fan, state.swing) == (21, P.Fan.AUTO, "off")      # valeurs par défaut du chauffage
    state = change(state, temperature=26, fan=P.Fan.LOW, swing="swing_up")
    state = change(state, mode=P.Mode.DRY)
    assert state.fan == P.Fan.LOW and state.xfan is True       # ventilo imposé, X-Fan par défaut
    state = change(state, mode=P.Mode.AUTO)
    assert state.temperature == 23 and not state.xfan
    mem2 = json.loads(json.dumps(mem))                         # la mémoire survit au stockage JSON
    state = change(state, mode=P.Mode.COOL)
    assert (state.temperature, state.fan, state.swing, state.xfan) == (22, P.Fan.HIGH, "fixed_up", False)
    assert P.recall(mem2, P.Mode.HEAT)["temperature"] == 26 and P.recall(mem2, P.Mode.HEAT)["fan"] == P.Fan.LOW
    assert "temperature" not in mem.get("auto", {}) and "fan" not in mem.get("dry", {})


if __name__ == "__main__":
    test_all_captures_regenerated_exactly()
    test_timer_fields_match_captures()
    test_timer_txt_values()
    test_no_timer_means_single_frame()
    test_all_states_valid_and_roundtrip()
    test_ifeel_captures()
    test_forced_rules_per_mode()
    test_auto_mode_wire_temperature_is_25_but_state_is_23()
    test_per_mode_memory()
    print("OK : 55 captures reproduites à l'identique")
