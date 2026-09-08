"""Leser fuer Mario-Kart-Wii-Ghostdateien (.rkg).

Liest ausschliesslich den 0x88 Byte grossen Header. Die Eingabedaten dahinter
werden bewusst nicht ausgewertet: fuer das Ghost-Harness ist nur die
Sollvorgabe interessant (Strecke, Endzeit, Rundenzeiten), und je weniger vom
Dateiinhalt angefasst wird, desto klarer bleibt, dass hier nichts kopiert oder
weitergereicht wird.

WICHTIG: Dieses Modul oeffnet nur Pfade, die ihm der Aufrufer nennt. Es legt
keine Kopien an, schreibt nichts zurueck und gibt nie Rohbytes aus.

Feldbelegung des Headers
------------------------
Die folgenden Offsets sind gegen echte Ghostdateien verifiziert (siehe
`validate_layout` und `README.md`, Abschnitt "Verifikation"). Bereiche, deren
Bedeutung hier nicht sicher bestimmt werden konnte, bleiben absichtlich
unbenannt und werden nicht interpretiert.

    0x00  4     Magic "RKGD"
    0x04  4     Bitfeld: Minuten(7) Sekunden(7) Millisekunden(10)
                          Strecke(6) unbenannt(2)
    0x08  4     Bitfeld: Fahrzeug(6) Charakter(6) Jahr(7) Monat(4)
                          Tag(5) Controller(4)
    0x0C  2     Bitfeld: unbenannt(4) komprimiert(1) unbenannt(2)
                          Ghost-Typ(7) Drift-Typ(1) unbenannt(1)
    0x0E  2     Laenge der Eingabedaten
    0x10  1     Rundenzahl
    0x11  15    5 Rundenzeiten zu je 3 Byte
    0x20  0x68  nicht ermittelt (Mii-Daten, Herkunft, Pruefsummen)
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import BinaryIO

MAGIC = b"RKGD"
HEADER_SIZE = 0x88
MAX_LAPS = 5
LAP_TABLE_OFFSET = 0x11
LAP_ENTRY_SIZE = 3


class RkgError(ValueError):
    """Die Datei ist keine brauchbare Ghostdatei."""


@dataclasses.dataclass(frozen=True)
class Time:
    """Eine Zeitangabe, so wie das Spiel sie speichert."""

    minutes: int
    seconds: int
    milliseconds: int

    @property
    def total_milliseconds(self) -> int:
        return (self.minutes * 60 + self.seconds) * 1000 + self.milliseconds

    def is_plausible(self) -> bool:
        return (
            0 <= self.minutes < 100
            and 0 <= self.seconds < 60
            and 0 <= self.milliseconds < 1000
        )

    def __str__(self) -> str:
        return f"{self.minutes:d}:{self.seconds:02d}.{self.milliseconds:03d}"


@dataclasses.dataclass(frozen=True)
class Ghost:
    """Der ausgewertete Header einer Ghostdatei."""

    source: pathlib.Path
    finish_time: Time
    track_id: int
    vehicle_id: int
    character_id: int
    controller_id: int
    year: int
    month: int
    day: int
    compressed: bool
    ghost_type: int
    drift_type: int
    input_data_length: int
    lap_count: int
    lap_times: tuple[Time, ...]

    @property
    def lap_sum_milliseconds(self) -> int:
        return sum(lap.total_milliseconds for lap in self.lap_times)

    def summary(self) -> str:
        return (
            f"{self.source.name}: Strecke {self.track_id}, "
            f"{self.lap_count} Runden, Endzeit {self.finish_time}"
        )


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _unpack_time_24(raw: int) -> Time:
    """Rundenzeit aus 24 Bit: Minuten(7) Sekunden(7) Millisekunden(10)."""
    return Time(
        minutes=(raw >> 17) & 0x7F,
        seconds=(raw >> 10) & 0x7F,
        milliseconds=raw & 0x3FF,
    )


def parse_header(data: bytes, source: pathlib.Path | None = None) -> Ghost:
    """Wertet einen Header aus. `data` muss mindestens HEADER_SIZE Byte haben."""
    if len(data) < HEADER_SIZE:
        raise RkgError(
            f"Header zu kurz: {len(data)} Byte, erwartet mindestens {HEADER_SIZE}"
        )
    if data[:4] != MAGIC:
        raise RkgError(f"Kein RKGD-Magic, gefunden: {data[:4]!r}")

    word_time = _u32(data, 0x04)
    word_meta = _u32(data, 0x08)
    half_flags = _u16(data, 0x0C)

    lap_count = data[0x10]
    laps: list[Time] = []
    for index in range(MAX_LAPS):
        offset = LAP_TABLE_OFFSET + index * LAP_ENTRY_SIZE
        raw = int.from_bytes(data[offset : offset + LAP_ENTRY_SIZE], "big")
        laps.append(_unpack_time_24(raw))

    return Ghost(
        source=source if source is not None else pathlib.Path("<bytes>"),
        finish_time=Time(
            minutes=(word_time >> 25) & 0x7F,
            seconds=(word_time >> 18) & 0x7F,
            milliseconds=(word_time >> 8) & 0x3FF,
        ),
        track_id=(word_time >> 2) & 0x3F,
        vehicle_id=(word_meta >> 26) & 0x3F,
        character_id=(word_meta >> 20) & 0x3F,
        year=2000 + ((word_meta >> 13) & 0x7F),
        month=(word_meta >> 9) & 0xF,
        day=(word_meta >> 4) & 0x1F,
        controller_id=word_meta & 0xF,
        compressed=bool((half_flags >> 11) & 1),
        ghost_type=(half_flags >> 2) & 0x7F,
        drift_type=(half_flags >> 1) & 1,
        input_data_length=_u16(data, 0x0E),
        lap_count=lap_count,
        # Nur so viele Rundenzeiten zurueckgeben, wie das Rennen Runden hatte;
        # die restlichen Tabellenplaetze sind unbenutzt und nicht aussagekraeftig.
        lap_times=tuple(laps[: min(lap_count, MAX_LAPS)]),
    )


def read(path: pathlib.Path | str) -> Ghost:
    """Liest den Header einer Ghostdatei. Nur die ersten 0x88 Byte."""
    path = pathlib.Path(path)
    with path.open("rb") as handle:
        return parse_header(handle.read(HEADER_SIZE), source=path)


def read_stream(handle: BinaryIO, source: pathlib.Path | None = None) -> Ghost:
    return parse_header(handle.read(HEADER_SIZE), source=source)


# --------------------------------------------------------------------------
# Verifikation der Feldbelegung
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class LayoutCheck:
    """Ergebnis der Plausibilitaetspruefung eines Headers."""

    ok: bool
    problems: tuple[str, ...]

    def __bool__(self) -> bool:
        return self.ok


def validate_layout(ghost: Ghost, lap_sum_tolerance_ms: int = 2) -> LayoutCheck:
    """Prueft, ob die geparsten Felder in sich stimmig sind.

    Der Zweck ist nicht, eine Ghostdatei zu bewerten, sondern die oben
    angenommene Feldbelegung zu belegen: sind Zeiten im gueltigen Bereich,
    liegt die Rundenzahl im moeglichen Bereich und summieren sich die
    Rundenzeiten auf die Endzeit, dann sitzen die Bitfelder richtig. Ueber
    viele Dateien hinweg ist das ein empirischer Nachweis statt einer Annahme.
    """
    problems: list[str] = []

    if not ghost.finish_time.is_plausible():
        problems.append(f"Endzeit unplausibel: {ghost.finish_time}")

    if not 1 <= ghost.lap_count <= MAX_LAPS:
        problems.append(f"Rundenzahl ausserhalb 1..{MAX_LAPS}: {ghost.lap_count}")

    for index, lap in enumerate(ghost.lap_times, start=1):
        if not lap.is_plausible():
            problems.append(f"Rundenzeit {index} unplausibel: {lap}")

    if not 1 <= ghost.month <= 12:
        problems.append(f"Monat ausserhalb 1..12: {ghost.month}")
    if not 1 <= ghost.day <= 31:
        problems.append(f"Tag ausserhalb 1..31: {ghost.day}")

    # Die eigentliche Probe: Rundenzeiten muessen die Endzeit ergeben.
    if 1 <= ghost.lap_count <= MAX_LAPS and ghost.finish_time.is_plausible():
        difference = abs(ghost.lap_sum_milliseconds - ghost.finish_time.total_milliseconds)
        if difference > lap_sum_tolerance_ms:
            problems.append(
                "Summe der Rundenzeiten weicht von der Endzeit ab: "
                f"{ghost.lap_sum_milliseconds} ms gegen "
                f"{ghost.finish_time.total_milliseconds} ms "
                f"(Differenz {difference} ms)"
            )

    return LayoutCheck(ok=not problems, problems=tuple(problems))
