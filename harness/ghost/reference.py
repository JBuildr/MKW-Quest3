"""Referenzlaeufe: aufzeichnen, speichern, vergleichen.

Das ist der eigentliche Abnahmemechanismus. Ein Referenzlauf haelt fest, was
beim Abspielen einer Ghostdatei herauskam; ein spaeterer Lauf auf einer anderen
Architektur muss dasselbe liefern. Der Vergleich ist **exakt** - Physikparitaet
ist laut Spezifikation nicht verhandelbar, also gibt es hier keine Toleranz auf
Positionen.

Inhalt einer Referenzdatei
--------------------------
Ausschliesslich Messwerte und Metadaten aus dem eigenen Lauf des Nutzers:
Streckennummer, Rundenzahl, Zeiten und Pruefsummen. **Kein Spielinhalt**, keine
Eingabedaten der Ghostdatei, keine Speicherinhalte - Pruefsummen sind
Einwegwerte, aus denen sich nichts rekonstruieren laesst.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import pathlib
import platform
import sys
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 2


@dataclasses.dataclass(frozen=True)
class Sample:
    """Eine Stichprobe des Gastzustands zu einem Zeitpunkt.

    `frame` zaehlt **ab Beginn der Aufzeichnung**, nicht absolut. Der
    Framezaehler des Spiels laeuft ueber Rennen und Menues hinweg durch und hat
    in zwei Laeufen desselben Ghosts voellig verschiedene Werte; absolute
    Nummern liessen sich also nie vergleichen.
    """

    frame: int
    checksum: str
    # Optional, nur zur Fehlersuche: je Feld eine eigene Pruefsumme. Damit
    # laesst sich beantworten, WELCHES Feld zwischen zwei Laeufen abweicht,
    # statt nur dass irgendeines es tut.
    fields: dict[str, str] | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"frame": self.frame, "checksum": self.checksum}
        if self.fields:
            payload["fields"] = self.fields
        return payload

    @staticmethod
    def from_json(raw: dict[str, Any]) -> "Sample":
        return Sample(
            frame=int(raw["frame"]),
            checksum=str(raw["checksum"]),
            fields=raw.get("fields"),
        )


@dataclasses.dataclass(frozen=True)
class RunResult:
    """Das Ergebnis eines Ghost-Abspiellaufs."""

    ghost_name: str
    track_id: int
    lap_count: int
    expected_finish_ms: int
    measured_finish_ms: int | None
    samples: tuple[Sample, ...]
    # Der absolute Zaehlerstand beim ersten Abzug. Nur zur Nachvollziehbarkeit -
    # er geht bewusst NICHT in den Vergleich ein.
    first_frame_absolute: int = 0
    host: dict[str, Any] = dataclasses.field(default_factory=dict)
    recorded_at: str = ""

    @property
    def finish_matches_ghost(self) -> bool:
        return (
            self.measured_finish_ms is not None
            and self.measured_finish_ms == self.expected_finish_ms
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "ghost_name": self.ghost_name,
            "track_id": self.track_id,
            "lap_count": self.lap_count,
            "expected_finish_ms": self.expected_finish_ms,
            "measured_finish_ms": self.measured_finish_ms,
            "samples": [s.to_json() for s in self.samples],
            "first_frame_absolute": self.first_frame_absolute,
            "host": self.host,
            "recorded_at": self.recorded_at,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> "RunResult":
        version = int(raw.get("schema_version", 0))
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"Referenz hat Schema-Version {version}, erwartet {SCHEMA_VERSION}. "
                "Neu aufzeichnen statt umrechnen - eine stillschweigend "
                "migrierte Referenz ist als Abnahmekriterium wertlos."
            )
        measured = raw.get("measured_finish_ms")
        return RunResult(
            ghost_name=str(raw["ghost_name"]),
            track_id=int(raw["track_id"]),
            lap_count=int(raw["lap_count"]),
            expected_finish_ms=int(raw["expected_finish_ms"]),
            measured_finish_ms=None if measured is None else int(measured),
            samples=tuple(Sample.from_json(s) for s in raw.get("samples", [])),
            first_frame_absolute=int(raw.get("first_frame_absolute", 0)),
            host=dict(raw.get("host", {})),
            recorded_at=str(raw.get("recorded_at", "")),
        )


def host_description() -> dict[str, Any]:
    """Beschreibt den Rechner, auf dem gemessen wurde.

    Gehoert in jede Referenz: eine Abweichung zwischen x86 und aarch64 ist nur
    dann aussagekraeftig, wenn dokumentiert ist, was verglichen wurde.
    """
    return {
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def checksum_positions(values: Iterable[float | int]) -> str:
    """Pruefsumme ueber eine Folge von Messwerten.

    Floats werden ueber ihre exakte Bitdarstellung gehasht, nicht ueber eine
    formatierte Zahl: eine Abweichung im letzten Bit ist genau das, was dieses
    Harness finden soll, und duerfte durch keine Textformatierung verschwinden.
    """
    import struct

    digest = hashlib.sha256()
    for value in values:
        if isinstance(value, float):
            digest.update(struct.pack(">d", value))
        else:
            digest.update(struct.pack(">q", int(value)))
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Speichern und Laden
# --------------------------------------------------------------------------


def save(result: RunResult, path: pathlib.Path | str) -> pathlib.Path:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_json()
    if not payload["recorded_at"]:
        payload["recorded_at"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(timespec="seconds")
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def load(path: pathlib.Path | str) -> RunResult:
    path = pathlib.Path(path)
    return RunResult.from_json(json.loads(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# Vergleich
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Comparison:
    """Das Urteil: passt der Lauf zur Referenz?"""

    passed: bool
    differences: tuple[str, ...]
    checked_samples: int

    def __bool__(self) -> bool:
        return self.passed

    def report(self) -> str:
        if self.passed:
            return (
                f"GRUEN - Lauf stimmt mit der Referenz ueberein "
                f"({self.checked_samples} Stichproben)"
            )
        lines = [f"ROT - {len(self.differences)} Abweichung(en):"]
        lines.extend(f"  - {d}" for d in self.differences)
        return "\n".join(lines)


def compare(reference: RunResult, candidate: RunResult) -> Comparison:
    """Vergleicht einen Lauf gegen die Referenz. Exakt, ohne Toleranz."""
    differences: list[str] = []

    if reference.ghost_name != candidate.ghost_name:
        differences.append(
            f"andere Ghostdatei: Referenz '{reference.ghost_name}', "
            f"Lauf '{candidate.ghost_name}'"
        )
    if reference.track_id != candidate.track_id:
        differences.append(
            f"andere Strecke: Referenz {reference.track_id}, "
            f"Lauf {candidate.track_id}"
        )
    if reference.lap_count != candidate.lap_count:
        differences.append(
            f"andere Rundenzahl: Referenz {reference.lap_count}, "
            f"Lauf {candidate.lap_count}"
        )

    if reference.measured_finish_ms != candidate.measured_finish_ms:
        differences.append(
            f"Endzeit weicht ab: Referenz {reference.measured_finish_ms} ms, "
            f"Lauf {candidate.measured_finish_ms} ms"
        )

    reference_by_frame = {s.frame: s.checksum for s in reference.samples}
    candidate_by_frame = {s.frame: s.checksum for s in candidate.samples}

    missing = sorted(set(reference_by_frame) - set(candidate_by_frame))
    if missing:
        shown = ", ".join(str(f) for f in missing[:8])
        suffix = " ..." if len(missing) > 8 else ""
        differences.append(
            f"{len(missing)} Stichprobe(n) fehlen im Lauf (Frames {shown}{suffix})"
        )

    extra = sorted(set(candidate_by_frame) - set(reference_by_frame))
    if extra:
        shown = ", ".join(str(f) for f in extra[:8])
        suffix = " ..." if len(extra) > 8 else ""
        differences.append(
            f"{len(extra)} zusaetzliche Stichprobe(n) im Lauf (Frames {shown}{suffix})"
        )

    shared = sorted(set(reference_by_frame) & set(candidate_by_frame))
    mismatched = [
        frame
        for frame in shared
        if reference_by_frame[frame] != candidate_by_frame[frame]
    ]
    if mismatched:
        first = mismatched[0]
        differences.append(
            f"{len(mismatched)} von {len(shared)} Stichproben weichen ab; "
            f"erste Abweichung bei Frame {first} "
            f"(Referenz {reference_by_frame[first][:16]}..., "
            f"Lauf {candidate_by_frame[first][:16]}...)"
        )

    return Comparison(
        passed=not differences,
        differences=tuple(differences),
        checked_samples=len(shared),
    )


def first_divergence_frame(
    reference: RunResult, candidate: RunResult
) -> int | None:
    """Der frueheste Frame, in dem die beiden Laeufe auseinanderlaufen.

    Fuer die Fehlersuche in Phase 1 der eigentlich interessante Wert: er sagt,
    wo man beim Vergleich zweier Architekturen ansetzen muss.
    """
    reference_by_frame = {s.frame: s.checksum for s in reference.samples}
    for sample in sorted(candidate.samples, key=lambda s: s.frame):
        expected = reference_by_frame.get(sample.frame)
        if expected is not None and expected != sample.checksum:
            return sample.frame
    return None


def summarize(results: Sequence[RunResult]) -> str:
    lines = []
    for result in results:
        status = "ok" if result.finish_matches_ghost else "ABWEICHUNG"
        lines.append(
            f"  {result.ghost_name}: Strecke {result.track_id}, "
            f"erwartet {result.expected_finish_ms} ms, "
            f"gemessen {result.measured_finish_ms} ms [{status}]"
        )
    return "\n".join(lines)
