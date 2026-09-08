"""Aufnahmeteil des Adress-Scanners: zwei Phasen, dann Auswertung.

Der Ablauf ist so gebaut, dass waehrend der Fahrt **keine Taste** gedrueckt
werden muss - es laeuft ein Countdown, damit beide Haende am Controller
bleiben koennen.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

import numpy as np

import guest_memory
import scan


def _countdown(message: str, seconds: int) -> None:
    print(f"\n{message}")
    for remaining in range(seconds, 0, -1):
        print(f"  ... {remaining}", end="\r", flush=True)
        time.sleep(1.0)
    print("  ... los!      ")


def _capture(
    memory: guest_memory.GuestMemory,
    guest_start: int,
    size: int,
    count: int,
    interval: float,
    label: str,
) -> list[scan.Snapshot]:
    snapshots: list[scan.Snapshot] = []
    for index in range(count):
        snapshots.append(scan.read_region(memory, guest_start, size))
        print(f"  {label}: Abzug {index + 1}/{count}", end="\r", flush=True)
        if index + 1 < count:
            time.sleep(interval)
    usable = int(snapshots[-1].chunk_valid.sum())
    total = len(snapshots[-1].chunk_valid)
    print(
        f"  {label}: {count} Abzuege, {usable}/{total} Bloecke lesbar"
        f" ({usable * scan.CHUNK_SIZE // (1024 * 1024)} MiB)      "
    )
    return snapshots


def run(args) -> int:
    region_start = guest_memory.MEM1_CACHED_BASE
    region_size = guest_memory.MEM1_SIZE
    if args.region == "mem2":
        region_start = guest_memory.MEM2_CACHED_BASE
        region_size = guest_memory.MEM2_SIZE

    print("Adress-Scanner")
    print(f"  Prozess:  {args.pid}")
    print(f"  Region:   0x{region_start:08X} + {region_size // (1024 * 1024)} MiB")
    print(
        "\nAblauf: erst steht das Kart still, dann faehrst du. Der Scanner sucht\n"
        "Felder, die im Stand konstant bleiben und sich beim Fahren stetig aendern.\n"
        "Wichtig: Beide Phasen muessen IM RENNEN stattfinden, nicht im Menue."
    )

    try:
        memory = guest_memory.GuestMemory(args.pid, target=args.target)
    except guest_memory.GuestMemoryError as error:
        print(f"\nVerbindung fehlgeschlagen:\n{error}", file=sys.stderr)
        return 3

    with memory:
        _countdown(
            "Phase 1 von 2 - lass das Kart STEHEN (nicht beschleunigen, nicht lenken).",
            args.lead_in,
        )
        standstill = _capture(
            memory, region_start, region_size,
            args.standstill_samples, args.standstill_interval, "Stand",
        )

        _countdown(
            "Phase 2 von 2 - FAHR JETZT los, moeglichst gleichmaessig geradeaus.",
            args.lead_in,
        )
        motion = _capture(
            memory, region_start, region_size,
            args.motion_samples, args.motion_interval, "Fahrt",
        )

    print("\nAuswerten ...")
    counters = scan.find_counters(standstill + motion)
    vectors = scan.find_vec3_fields(standstill, motion, max_smoothness=args.smoothness)

    positions = scan.rank(vectors, "position")
    velocities = scan.rank(vectors, "velocity")
    pairs = scan.pair_velocity_with_position(positions, velocities)

    frames = [c for c in counters if c.kind == "frame"]
    millis = [c for c in counters if c.kind == "millisecond"]

    print("\n=== Ergebnis ===")
    print(f"Positionskandidaten:      {len(positions)}")
    print(f"Geschwindigkeitskandidaten: {len(velocities)}")
    print(f"Paare (nah beieinander):  {len(pairs)}")
    print(f"Framezaehler (~60/s):     {len(frames)}")
    print(f"Millisekundenuhr (~1000/s): {len(millis)}")

    limit = args.show
    if pairs:
        print("\nBeste Paare - hier zuerst nachsehen:")
        for position, velocity in pairs[:limit]:
            distance = velocity.guest_address - position.guest_address
            print(f"  Position     {position.describe()}")
            print(f"  Geschwindig. {velocity.describe()}   Abstand {distance:+d} Byte")
            print()
    else:
        if positions:
            print("\nPositionskandidaten:")
            for candidate in positions[:limit]:
                print(f"  {candidate.describe()}")
        if velocities:
            print("\nGeschwindigkeitskandidaten:")
            for candidate in velocities[:limit]:
                print(f"  {candidate.describe()}")

    if frames:
        print("Framezaehler:")
        for candidate in frames[:limit]:
            print(f"  {candidate.describe()}")
    if millis:
        print("Millisekundenuhr:")
        for candidate in millis[:limit]:
            print(f"  {candidate.describe()}")

    if not positions and not velocities and not counters:
        print(
            "\nNichts gefunden. Haeufigste Ursachen:\n"
            "  - Eine der beiden Phasen lag im Menue statt im Rennen.\n"
            "  - Das Kart hat sich in Phase 1 doch bewegt (auch Rollen zaehlt).\n"
            "  - Die Daten liegen in MEM2: nochmal mit --region mem2 versuchen."
        )
        return 1

    if args.out:
        _write_proposal(pathlib.Path(args.out), pairs, positions, velocities, frames, millis)

    print(
        "\nNaechster Schritt: Adressen eintragen, dann mit\n"
        f"  python harness/ghost/ghostrun.py sample --pid {args.pid}\n"
        "gegenpruefen - Stand muss konstant sein, Fahrt muss sich aendern.\n"
        "Erst danach in der Adresskarte \"verified\": true setzen."
    )
    return 0


def _write_proposal(path, pairs, positions, velocities, frames, millis) -> None:
    """Schreibt einen Vorschlag - ausdruecklich unverifiziert."""
    def address(entries, index=0):
        return f"0x{entries[index].guest_address:08X}" if entries else ""

    if pairs:
        position_address = f"0x{pairs[0][0].guest_address:08X}"
        velocity_address = f"0x{pairs[0][1].guest_address:08X}"
    else:
        position_address = address(positions)
        velocity_address = address(velocities)

    payload = {
        "schema_version": 1,
        "game_id": "RMCP01",
        "verified": False,
        "verified_by": "",
        "verified_note": (
            "VORSCHLAG DES SCANNERS, NICHT GEPRUEFT. Die Adressen stammen aus einer "
            "Messung an einem laufenden Spiel und sind plausibel, aber nicht "
            "bestaetigt. Mit 'ghostrun.py sample' gegenpruefen: im Stand muessen die "
            "Werte konstant bleiben, beim Fahren sich stetig aendern, und die "
            "Rennzeit muss zur Anzeige im Spiel passen. Erst dann verified auf true "
            "setzen und hier den Namen eintragen."
        ),
        "fields": [
            {"name": "race_frame", "address": address(frames), "kind": "u32",
             "note": "Framezaehler, vom Scanner ueber die Schrittweite ~60/s gefunden."},
            {"name": "player_position", "address": position_address, "kind": "vec3",
             "note": "Im Stand konstant, beim Fahren stetig veraendert."},
            {"name": "player_velocity", "address": velocity_address, "kind": "vec3",
             "note": "Im Stand nahe null, beim Fahren von null verschieden."},
            {"name": "race_timer_ms", "address": address(millis), "kind": "u32",
             "note": "Zaehler mit Schrittweite ~1000/s."},
        ],
    }
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nVorschlag geschrieben: {path}  (verified: false)")
