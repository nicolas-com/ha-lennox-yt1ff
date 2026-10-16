"""Commande infrarouge brute compatible avec la plateforme `infrared` de Home Assistant.

La bibliothèque ``infrared_protocols`` a changé d'API (v2.0.0, HA 2026.5+) :
  * avant : ``get_raw_timings()`` renvoie une liste d'objets ``Timing(high_us, low_us)``
  * depuis la v2 : la classe ``Timing`` n'existe plus et ``get_raw_timings()`` renvoie une
    liste plate d'entiers signés en µs (positif = émission, négatif = silence).
Ce module gère les deux cas et ne plante jamais à l'import.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

try:  # ancienne API
    from infrared_protocols import Timing  # type: ignore[attr-defined]
except ImportError:  # API v2 : plus de classe Timing
    Timing = None  # type: ignore[assignment,misc]

_Base: type | None = None
for _path in (
    "infrared_protocols",
    "infrared_protocols.commands",
    "homeassistant.components.infrared",
):
    try:
        _mod = __import__(_path, fromlist=["Command"])
    except ImportError:
        continue
    for _name in ("Command", "InfraredCommand"):
        if (_cls := getattr(_mod, _name, None)) is not None:
            _Base = _cls
            break
    if _Base is not None:
        break

if _Base is None:  # dernier recours : classe autonome (duck typing)
    _LOGGER.warning("Classe Command de infrared_protocols introuvable ; utilisation d'une classe autonome")

    class _Base:  # type: ignore[no-redef]
        modulation: int = 38000
        repeat_count: int = 0


class YT1FFCommand(_Base):  # type: ignore[misc,valid-type]
    """Séquence de durées (µs) déjà calculée par protocol.py."""

    def __init__(self, timings_us: list[tuple[int, int]], *, modulation: int, repeat_count: int = 0) -> None:
        for kwargs in (
            {"modulation": modulation, "repeat_count": repeat_count},
            {"repeat_count": repeat_count},
            {"modulation": modulation},
            {},
        ):
            try:
                super().__init__(**kwargs)
                break
            except TypeError:
                continue
        # Garantit la présence des attributs lus par les émetteurs (classes figées ou non)
        for attr, val in (("modulation", modulation), ("repeat_count", repeat_count)):
            if getattr(self, attr, None) != val:
                try:
                    setattr(self, attr, val)
                except Exception:  # noqa: BLE001
                    object.__setattr__(self, attr, val)
        object.__setattr__(self, "_yt1ff_timings", [(int(m), int(s)) for m, s in timings_us])

    def get_raw_timings(self):
        pairs = self._yt1ff_timings
        if Timing is not None:  # ancienne API
            try:
                return [Timing(high_us=m, low_us=s) for m, s in pairs]
            except TypeError:
                pass
        flat: list[int] = []
        for m, s in pairs:
            flat.append(m)
            flat.append(-s)
        return flat
