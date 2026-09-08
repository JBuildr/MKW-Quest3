"""Adress-Scanner: findet Positions-, Geschwindigkeits- und Zaehlerfelder
empirisch im Speicher eines laufenden Spiels.

Warum das geht ohne externe Adressquelle
----------------------------------------
Die gesuchten Felder verraten sich durch ihr **Verhalten**, nicht durch ihre
Adresse:

* Eine Weltposition steht still, solange das Kart steht, und aendert sich
  danach stetig - nie sprunghaft.
* Ein Geschwindigkeitsvektor ist im Stand praktisch null und waechst beim
  Fahren; sein Betrag folgt der Positionsaenderung.
* Ein Framezaehler steigt streng monoton mit ungefaehr 60 Schritten je Sekunde,
  eine Millisekundenuhr mit ungefaehr 1000.

Zwei Aufnahmephasen - einmal stehend, einmal fahrend - genuegen, um daraus
Kandidaten einzukreisen. Das ist eine Messung, keine Vermutung: jeder Kandidat
muss sich in beiden Phasen richtig verhalten haben.

Das Ergebnis bleibt trotzdem ein **Vorschlag**. Der Scanner setzt die
Adresskarte nie auf `verified`; das bleibt eine menschliche Entscheidung nach
einer Gegenprobe mit `sample`.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Sequence

import numpy as np

CHUNK_SIZE = 0x10000  # 64 KiB - die Koernigkeit, in der gelesen und verworfen wird

# Erwartete Schrittweiten je Sekunde, mit grosszuegigen Baendern.
FRAME_RATE_BAND = (45.0, 75.0)      # Framezaehler bei 60 fps
MILLISECOND_RATE_BAND = (850.0, 1150.0)  # Millisekundenuhr

# Plausibilitaetsband fuer eine Weltkoordinate. Bewusst weit: es soll nur
# offensichtlichen Unsinn ausschliessen (Nullen, Denormale, Adressen als Float
# gelesen), nicht eine bestimmte Strecke voraussetzen.
POSITION_MIN_NORM = 1.0
POSITION_MAX_COMPONENT = 1.0e6

# Ein Geschwindigkeitsvektor ist im Stand nahe null.
VELOCITY_STANDSTILL_MAX_NORM = 0.5


@dataclasses.dataclass
class Snapshot:
    """Ein Abzug einer Speicherregion samt Zeitstempel und Gueltigkeitsmaske."""

    guest_start: int
    data: bytes
    chunk_valid: np.ndarray  # bool je CHUNK_SIZE-Block
    timestamp: float

    @property
    def element_count(self) -> int:
        return len(self.data) // 4

    def as_f32(self) -> np.ndarray:
        return np.frombuffer(self.data, dtype=">f4", count=self.element_count)

    def as_u32(self) -> np.ndarray:
        return np.frombuffer(self.data, dtype=">u4", count=self.element_count)

    def element_valid(self) -> np.ndarray:
        """Gueltigkeitsmaske je 4-Byte-Element."""
        indices = np.arange(self.element_count, dtype=np.int64)
        return self.chunk_valid[(indices * 4) // CHUNK_SIZE]


def read_region(memory, guest_start: int, size: int) -> Snapshot:
    """Liest eine Gastregion blockweise und uebersteht geschuetzte Seiten.

    Der Gast-View schuetzt MMIO-, EFB- und Executable-Seiten absichtlich
    (`runtime/include/guest_flat_memory.h:4-7`), ein Lesefehler ist dort also
    normal und kein Grund abzubrechen. Nicht lesbare Bloecke werden als
    ungueltig markiert und aus der Auswertung genommen.
    """
    chunk_count = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
    buffer = bytearray(chunk_count * CHUNK_SIZE)
    valid = np.zeros(chunk_count, dtype=bool)

    for index in range(chunk_count):
        offset = index * CHUNK_SIZE
        length = min(CHUNK_SIZE, size - offset)
        if length <= 0:
            break
        try:
            block = memory.read_bytes(guest_start + offset, length)
        except Exception:
            continue
        buffer[offset : offset + length] = block
        valid[index] = True

    return Snapshot(
        guest_start=guest_start,
        data=bytes(buffer),
        chunk_valid=valid,
        timestamp=time.monotonic(),
    )


# --------------------------------------------------------------------------
# Auswertung
# --------------------------------------------------------------------------


def common_valid_mask(snapshots: Sequence[Snapshot]) -> np.ndarray:
    """Elemente, die in **allen** Abzuegen gelesen werden konnten."""
    mask = snapshots[0].element_valid()
    for snapshot in snapshots[1:]:
        mask = mask & snapshot.element_valid()
    return mask


def unchanged_mask(snapshots: Sequence[Snapshot]) -> np.ndarray:
    """Elemente, deren Bitmuster ueber alle Abzuege identisch blieb.

    Verglichen wird das rohe u32-Muster, nicht der Float-Wert: sonst wuerde ein
    NaN nie sich selbst gleichen und stille Kandidaten gingen verloren.
    """
    first = snapshots[0].as_u32()
    mask = np.ones(len(first), dtype=bool)
    for snapshot in snapshots[1:]:
        mask &= first == snapshot.as_u32()
    return mask


def plausible_vec3_mask(values: np.ndarray) -> np.ndarray:
    """Elemente i, bei denen (i, i+1, i+2) wie eine Weltkoordinate aussehen."""
    finite = np.isfinite(values)
    bounded = np.abs(values) <= POSITION_MAX_COMPONENT
    # Denormale und exakte Nullen einzeln zulassen, aber der Vektor darf nicht
    # insgesamt winzig sein - das pruefen wir gleich ueber die Norm.
    usable = finite & bounded

    count = len(values) - 2
    if count <= 0:
        return np.zeros(0, dtype=bool)

    triple_usable = usable[0:count] & usable[1 : count + 1] & usable[2 : count + 2]

    x = values[0:count].astype(np.float64)
    y = values[1 : count + 1].astype(np.float64)
    z = values[2 : count + 2].astype(np.float64)
    norm = np.sqrt(x * x + y * y + z * z)

    return triple_usable & (norm >= POSITION_MIN_NORM)


def vec3_norms(snapshot: Snapshot) -> np.ndarray:
    values = snapshot.as_f32().astype(np.float64)
    count = len(values) - 2
    x = values[0:count]
    y = values[1 : count + 1]
    z = values[2 : count + 2]
    return np.sqrt(x * x + y * y + z * z)


@dataclasses.dataclass(frozen=True)
class CounterCandidate:
    guest_address: int
    rate_per_second: float
    kind: str  # "frame" oder "millisecond"

    def describe(self) -> str:
        return (
            f"0x{self.guest_address:08X}  {self.kind:11s} "
            f"{self.rate_per_second:8.1f}/s"
        )


def find_counters(snapshots: Sequence[Snapshot]) -> list[CounterCandidate]:
    """Streng monoton steigende u32-Werte mit passender Schrittweite."""
    if len(snapshots) < 3:
        return []

    valid = common_valid_mask(snapshots)
    values = [s.as_u32().astype(np.int64) for s in snapshots]

    increasing = np.ones(len(values[0]), dtype=bool)
    for earlier, later in zip(values, values[1:]):
        increasing &= later > earlier

    elapsed = snapshots[-1].timestamp - snapshots[0].timestamp
    if elapsed <= 0:
        return []
    rate = (values[-1] - values[0]) / elapsed

    frame_band = (rate >= FRAME_RATE_BAND[0]) & (rate <= FRAME_RATE_BAND[1])
    ms_band = (rate >= MILLISECOND_RATE_BAND[0]) & (rate <= MILLISECOND_RATE_BAND[1])

    candidates: list[CounterCandidate] = []
    for mask, kind in ((frame_band, "frame"), (ms_band, "millisecond")):
        for index in np.flatnonzero(valid & increasing & mask):
            candidates.append(
                CounterCandidate(
                    guest_address=snapshots[0].guest_start + int(index) * 4,
                    rate_per_second=float(rate[index]),
                    kind=kind,
                )
            )
    return candidates


# Eine Fahrt geht irgendwohin: die Luftlinie zwischen Anfang und Ende ist ein
# nennenswerter Teil des zurueckgelegten Wegs. Ein zufaellig zappelnder Wert
# kehrt dagegen staendig um und kommt kaum vom Fleck. Das trennt beide
# zuverlaessiger als die reine Schrittgroesse.
POSITION_MIN_DIRECTNESS = 0.35

# Beim Fahren muss ein Geschwindigkeitsvektor deutlich von null verschieden sein.
VELOCITY_MOTION_MIN_NORM = 1.0


@dataclasses.dataclass(frozen=True)
class Vec3Candidate:
    guest_address: int
    standstill_norm: float
    motion_total_distance: float
    motion_mean_norm: float
    smoothness: float   # groesster Schritt geteilt durch Medianschritt
    directness: float   # Luftlinie geteilt durch Weglaenge
    varying_components: int  # wie viele der drei Achsen sich bewegen
    role: str           # "position" oder "velocity"

    def describe(self) -> str:
        if self.role == "position":
            return (
                f"0x{self.guest_address:08X}  position  "
                f"Weg={self.motion_total_distance:10.2f}  "
                f"Geradlinigkeit={self.directness:4.2f}  "
                f"bewegte Achsen={self.varying_components}/3  "
                f"Gleichmaessigkeit={self.smoothness:5.2f}"
            )
        return (
            f"0x{self.guest_address:08X}  velocity  "
            f"Stand |v|={self.standstill_norm:6.3f}  "
            f"Fahrt |v|={self.motion_mean_norm:9.2f}"
        )


def find_vec3_fields(
    standstill: Sequence[Snapshot],
    motion: Sequence[Snapshot],
    max_smoothness: float = 25.0,
    min_directness: float = POSITION_MIN_DIRECTNESS,
) -> list[Vec3Candidate]:
    """Findet Positions- und Geschwindigkeitsvektoren.

    Die beiden werden an unterschiedlichen Merkmalen erkannt, nicht am selben:

    * **Position** steht im Stand still und legt beim Fahren einen Weg zurueck,
      der ueberwiegend in eine Richtung fuehrt.
    * **Geschwindigkeit** ist im Stand nahe null und beim Fahren deutlich von
      null verschieden. Sie muss sich dabei *nicht* aendern - bei konstanter
      Fahrt bleibt sie zu Recht gleich.
    """
    if len(standstill) < 2 or len(motion) < 3:
        return []

    element_valid = common_valid_mask(list(standstill) + list(motion))
    count = len(standstill[0].as_f32()) - 2
    if count <= 0:
        return []

    def triple(mask: np.ndarray) -> np.ndarray:
        return mask[0:count] & mask[1 : count + 1] & mask[2 : count + 2]

    valid_triple = triple(element_valid)
    frozen_triple = triple(unchanged_mask(standstill))
    plausible = plausible_vec3_mask(standstill[-1].as_f32())
    standstill_norm = vec3_norms(standstill[-1])

    # Beide Rollen sind im Stand konstant; der Betrag im Stand trennt sie.
    base = valid_triple & frozen_triple
    seed = base & (plausible | (standstill_norm <= VELOCITY_STANDSTILL_MAX_NORM))

    indices = np.flatnonzero(seed)
    if indices.size == 0:
        return []

    tracks = []
    for snapshot in motion:
        values = snapshot.as_f32().astype(np.float64)
        tracks.append(
            np.stack([values[indices], values[indices + 1], values[indices + 2]], axis=1)
        )
    series = np.stack(tracks, axis=0)  # (Zeit, Kandidat, 3)

    finite_rows = np.all(np.isfinite(series), axis=(0, 2))
    indices = indices[finite_rows]
    series = series[:, finite_rows, :]
    if indices.size == 0:
        return []

    steps = np.linalg.norm(np.diff(series, axis=0), axis=2)   # (Zeit-1, Kandidat)
    path = steps.sum(axis=0)
    crow = np.linalg.norm(series[-1] - series[0], axis=1)     # Luftlinie
    motion_norm = np.linalg.norm(series, axis=2).mean(axis=0)
    # Ein echter Ortsvektor bewegt sich auf mehreren Achsen. Ein um vier Byte
    # verschobenes Tripel erwischt nur eine echte Komponente und daneben
    # Nachbardaten, die oft stillstehen - daran lassen sich die beiden trennen.
    varying = (series.max(axis=0) - series.min(axis=0)) > 0.0
    varying_components = varying.sum(axis=1)

    median_step = np.median(steps, axis=0)
    largest_step = steps.max(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        smoothness = np.where(median_step > 0, largest_step / median_step, np.inf)
        directness = np.where(path > 0, crow / path, 0.0)

    rest_norm = standstill_norm[indices]

    is_position = (
        (path > 0.0)
        & np.all(steps > 0.0, axis=0)
        & np.isfinite(smoothness)
        & (smoothness <= max_smoothness)
        & (directness >= min_directness)
        & (rest_norm > VELOCITY_STANDSTILL_MAX_NORM)
    )
    is_velocity = (rest_norm <= VELOCITY_STANDSTILL_MAX_NORM) & (
        motion_norm >= VELOCITY_MOTION_MIN_NORM
    )

    results: list[Vec3Candidate] = []
    for position, index in enumerate(indices):
        if is_position[position]:
            role = "position"
        elif is_velocity[position]:
            role = "velocity"
        else:
            continue
        results.append(
            Vec3Candidate(
                guest_address=standstill[0].guest_start + int(index) * 4,
                standstill_norm=float(rest_norm[position]),
                motion_total_distance=float(path[position]),
                motion_mean_norm=float(motion_norm[position]),
                smoothness=float(smoothness[position]),
                directness=float(directness[position]),
                varying_components=int(varying_components[position]),
                role=role,
            )
        )
    return results


def rank(candidates: Sequence[Vec3Candidate], role: str) -> list[Vec3Candidate]:
    """Sortiert nach Brauchbarkeit: geradlinige Fahrt zuerst."""
    chosen = [c for c in candidates if c.role == role]
    if role == "velocity":
        return sorted(chosen, key=lambda c: (c.standstill_norm, -c.motion_mean_norm))
    return sorted(
        chosen,
        key=lambda c: (-c.varying_components, -c.directness, c.smoothness),
    )


def pair_velocity_with_position(
    positions: Sequence[Vec3Candidate],
    velocities: Sequence[Vec3Candidate],
    max_distance: int = 0x400,
) -> list[tuple[Vec3Candidate, Vec3Candidate]]:
    """Paart Geschwindigkeiten mit nahe liegenden Positionen.

    In einer Spielstruktur liegen zusammengehoerige Felder meist im selben
    Objekt, also nur wenige hundert Byte auseinander. Ein Paar ist deshalb ein
    deutlich staerkerer Hinweis als zwei Einzelfunde.

    `positions` und `velocities` werden als **bereits sortiert** erwartet (siehe
    `rank`). Die Reihenfolge der Paare folgt zuerst dieser Guete und erst danach
    dem Abstand: ein knapp danebenliegendes Tripel, das zufaellig naeher am
    Geschwindigkeitsfeld sitzt, darf den besseren Kandidaten nicht verdraengen.
    """
    pairs: list[tuple[int, int, int, Vec3Candidate, Vec3Candidate]] = []
    for position_rank, position in enumerate(positions):
        for velocity_rank, velocity in enumerate(velocities):
            distance = abs(velocity.guest_address - position.guest_address)
            if distance <= max_distance:
                pairs.append((position_rank, velocity_rank, distance, position, velocity))
    pairs.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return [(position, velocity) for _, _, _, position, velocity in pairs]


# --------------------------------------------------------------------------
# Zeigersuche
# --------------------------------------------------------------------------

# Grobe Obergrenze des statisch geladenen Bereichs. main.dol liegt ab
# 0x80004000, StaticR.rel wird laut projects/mkwii/recomp.yml nach 0x805102E0
# geladen; darueber beginnt irgendwo der Heap. Der Wert dient nur zum
# **Einordnen** in der Ausgabe, nicht zum Filtern - wo genau die Grenze liegt,
# ist nicht ermittelt.
LIKELY_STATIC_BELOW = 0x8070_0000


@dataclasses.dataclass(frozen=True)
class PointerHop:
    """Ein Zeiger, der (mit Versatz) auf ein Ziel fuehrt."""

    at: int        # Gastadresse, an der der Zeiger steht
    value: int     # Gastadresse, auf die er zeigt
    offset: int    # Ziel = value + offset

    @property
    def looks_static(self) -> bool:
        return self.at < LIKELY_STATIC_BELOW


@dataclasses.dataclass(frozen=True)
class PointerChain:
    """Eine Kette vom statischen Bereich bis zum gesuchten Feld."""

    hops: tuple[PointerHop, ...]

    @property
    def root(self) -> PointerHop:
        return self.hops[-1]

    @property
    def depth(self) -> int:
        return len(self.hops)

    def describe(self) -> str:
        marker = "statisch" if self.root.looks_static else "Heap"
        path = " -> ".join(
            f"[0x{hop.at:08X}]{f'+0x{hop.offset:X}' if hop.offset else ''}"
            for hop in reversed(self.hops)
        )
        return f"{marker:8s} Tiefe {self.depth}  {path}"


def pointer_values(snapshot: Snapshot) -> np.ndarray:
    return snapshot.as_u32()


def find_pointers_to(
    snapshot: Snapshot,
    target: int,
    max_offset: int = 0x1000,
) -> list[PointerHop]:
    """Alle 4-Byte-Woerter, die auf `target` oder kurz davor zeigen.

    "Kurz davor" faengt den ueblichen Fall ab, dass ein Zeiger auf den Anfang
    eines Objekts zeigt und das gesuchte Feld ein Stueck weiter innen liegt.
    """
    values = pointer_values(snapshot).astype(np.int64)
    valid = snapshot.element_valid()
    low = target - max_offset
    hit = valid & (values <= target) & (values >= low)

    hops: list[PointerHop] = []
    for index in np.flatnonzero(hit):
        value = int(values[index])
        hops.append(
            PointerHop(
                at=snapshot.guest_start + int(index) * 4,
                value=value,
                offset=target - value,
            )
        )
    return hops


def find_pointer_chains(
    snapshot: Snapshot,
    target: int,
    max_offset: int = 0x1000,
    max_depth: int = 2,
    max_per_level: int = 40,
) -> list[PointerChain]:
    """Sucht Ketten vom statischen Bereich zum Ziel, breitensuchend.

    Abgebrochen wird, sobald eine Kette im wahrscheinlich statischen Bereich
    endet - tiefer zu suchen bringt dort nichts mehr.
    """
    finished: list[PointerChain] = []
    frontier: list[tuple[int, tuple[PointerHop, ...]]] = [(target, ())]
    seen: set[int] = {target}

    for _ in range(max_depth):
        next_frontier: list[tuple[int, tuple[PointerHop, ...]]] = []
        for current, prefix in frontier:
            hops = find_pointers_to(snapshot, current, max_offset)
            # Enge Treffer zuerst: ein kleiner Versatz ist der wahrscheinlichere
            # Objektanfang als ein zufaelliger Wert weit davor.
            hops.sort(key=lambda hop: (hop.offset, hop.at))
            for hop in hops[:max_per_level]:
                chain = prefix + (hop,)
                if hop.looks_static:
                    finished.append(PointerChain(hops=chain))
                elif hop.at not in seen:
                    seen.add(hop.at)
                    next_frontier.append((hop.at, chain))
        frontier = next_frontier
        if not frontier:
            break

    finished.sort(key=lambda chain: (chain.depth, chain.root.offset, chain.root.at))
    return finished
