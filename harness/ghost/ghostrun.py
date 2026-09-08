#!/usr/bin/env python3
"""Ghost-Harness - Kommandozeilenwerkzeug.

Abnahmekriterium fuer alle Phasen ab 1: eine Ghostdatei abspielen, Endzeit und
Positions-Pruefsummen gegen eine Referenz vergleichen.

Unterkommandos
--------------
    inspect        Header einer Ghostdatei anzeigen und pruefen
    verify-layout  Parser gegen eine ganze Ghost-Sammlung pruefen
    probe          Pruefen, ob der Gastspeicher eines Prozesses lesbar ist
    sample         Einmalig die Adresskarte abtasten (Diagnose)
    scan           Adressen empirisch am laufenden Spiel suchen
    pointerscan    Statischen Zeiger auf eine Heap-Adresse suchen
    record         Referenzlauf aufzeichnen
    check          Lauf gegen eine Referenz pruefen (Exitcode 0 oder 1)
    compare        Zwei gespeicherte Laeufe vergleichen, ohne Spiel

Keines dieser Kommandos kopiert jemals eine Ghostdatei oder Spieldaten. Pfade
kommen vom Aufrufer und werden nur gelesen.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import addressmap  # noqa: E402
import guest_memory  # noqa: E402
import reference  # noqa: E402
import rkg  # noqa: E402

DEFAULT_ADDRESS_MAP = HERE / "addresses" / "rmcp01-pal.json"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3


# --------------------------------------------------------------------------


def cmd_inspect(args: argparse.Namespace) -> int:
    ghost = rkg.read(args.ghost)
    check = rkg.validate_layout(ghost)

    print(f"Datei:         {ghost.source.name}")
    print(f"Strecke:       {ghost.track_id}")
    print(f"Fahrzeug:      {ghost.vehicle_id}")
    print(f"Charakter:     {ghost.character_id}")
    print(f"Controller:    {ghost.controller_id}")
    print(f"Aufgenommen:   {ghost.year:04d}-{ghost.month:02d}-{ghost.day:02d}")
    print(f"Runden:        {ghost.lap_count}")
    print(f"Endzeit:       {ghost.finish_time}  ({ghost.finish_time.total_milliseconds} ms)")
    for index, lap in enumerate(ghost.lap_times, start=1):
        print(f"  Runde {index}:     {lap}  ({lap.total_milliseconds} ms)")
    print(f"Rundensumme:   {ghost.lap_sum_milliseconds} ms")
    print(f"Komprimiert:   {'ja' if ghost.compressed else 'nein'}")
    print(f"Eingabelaenge: {ghost.input_data_length} Byte")

    if check:
        print("\nLayout-Pruefung: stimmig")
        return EXIT_OK
    print("\nLayout-Pruefung: Beanstandungen")
    for problem in check.problems:
        print(f"  - {problem}")
    return EXIT_MISMATCH


def cmd_verify_layout(args: argparse.Namespace) -> int:
    roots = [pathlib.Path(p) for p in args.directory]
    files: list[pathlib.Path] = []
    for root in roots:
        if not root.exists():
            print(f"Verzeichnis fehlt, uebersprungen: {root}", file=sys.stderr)
            continue
        files.extend(sorted(root.rglob("*.rkg")))

    if not files:
        print("Keine .rkg-Dateien gefunden.", file=sys.stderr)
        return EXIT_USAGE

    total = len(files)
    ok = 0
    unreadable = 0
    problems: dict[str, int] = {}
    tracks: set[int] = set()

    for path in files:
        try:
            ghost = rkg.read(path)
        except rkg.RkgError:
            unreadable += 1
            continue
        tracks.add(ghost.track_id)
        check = rkg.validate_layout(ghost)
        if check:
            ok += 1
        else:
            for problem in check.problems:
                key = problem.split(":")[0]
                problems[key] = problems.get(key, 0) + 1

    print(f"Geprueft:         {total}")
    print(f"Layout stimmig:   {ok}  ({100.0 * ok / total:.1f} %)")
    print(f"Nicht lesbar:     {unreadable}")
    if tracks:
        print(f"Strecken-IDs:     {len(tracks)} verschiedene, {min(tracks)}..{max(tracks)}")
    if problems:
        print("Beanstandungen:")
        for key, count in sorted(problems.items(), key=lambda kv: -kv[1]):
            print(f"  {count:5d}x  {key}")

    # Der Parser gilt als bestaetigt, wenn praktisch alles stimmig ist. Ein paar
    # Ausreisser sind kein Parserfehler, sondern Material mit anderer Herkunft.
    return EXIT_OK if ok >= total * 0.95 else EXIT_MISMATCH


def cmd_probe(args: argparse.Namespace) -> int:
    """Prueft, ob der flache Gastadressraum von aussen lesbar ist.

    Das ist die Grundannahme des ganzen Harness. Sie wird hier einzeln
    nachgewiesen, damit ein spaeterer Fehlschlag nicht faelschlich der
    Adresskarte angelastet wird.
    """
    try:
        with guest_memory.GuestMemory(args.pid, target=args.target) as memory:
            print(f"Prozess:       {args.pid}")
            print(f"Flache Basis:  0x{memory.flat_base:012X}")
            probe_address = guest_memory.MEM1_CACHED_BASE
            host = guest_memory.host_address(probe_address, args.target)
            print(f"Testadresse:   Gast 0x{probe_address:08X} -> Host 0x{host:012X}")
            data = memory.read_bytes(probe_address, 16)
            print(f"Gelesen:       {len(data)} Byte")
            print("\nErgebnis: Gastspeicher ist lesbar.")
            return EXIT_OK
    except guest_memory.GuestMemoryError as error:
        print(f"Ergebnis: nicht lesbar.\n{error}", file=sys.stderr)
        return EXIT_BLOCKED


def _load_verified_map(path: pathlib.Path) -> addressmap.AddressMap:
    address_map = addressmap.load(path)
    address_map.require_verified()
    return address_map


def cmd_sample(args: argparse.Namespace) -> int:
    try:
        address_map = _load_verified_map(pathlib.Path(args.address_map))
    except addressmap.AddressMapError as error:
        print(f"Adresskarte nicht nutzbar:\n{error}", file=sys.stderr)
        return EXIT_BLOCKED

    try:
        with guest_memory.GuestMemory(args.pid, target=args.target) as memory:
            named = addressmap.sample_named(memory, address_map)
            scored = addressmap.sample(
                memory, addressmap.checksum_fields(address_map)
            )
    except guest_memory.GuestMemoryError as error:
        print(f"Lesen fehlgeschlagen: {error}", file=sys.stderr)
        return EXIT_BLOCKED

    for name, value in named:
        print(f"  {name:20s} {value}")
    print(f"\nPruefsumme (ohne Zeitachse): {reference.checksum_positions(scored)}")
    return EXIT_OK


def _wait_for_motion(memory, address_map, frame_address, threshold, timeout):
    """Wartet, bis sich das Kart wirklich bewegt, und liefert diesen Frame.

    Der Startzeitpunkt der Aufzeichnung ist willkuerlich - er haengt daran, wann
    jemand das Kommando abschickt. Zwei Laeufe waeren damit nie vergleichbar.
    Deshalb wird nicht ab dem Kommando gezaehlt, sondern ab einem Ereignis im
    Spiel: dem ersten Frame, in dem sich die Position um mehr als `threshold`
    Einheiten aendert.

    Wichtig ist die Schwelle: das Kart zittert auch im Stand (Federung, sechste
    Nachkommastelle). "Erste Aenderung" waere deshalb sofort wahr und als Anker
    wertlos - erst eine echte Verschiebung zaehlt.
    """
    position_field = next(
        (f for f in address_map.fields if f.kind == "vec3" and f.in_checksum), None
    )
    if position_field is None:
        raise addressmap.AddressMapError(
            "Zum Ankern wird ein vec3-Feld gebraucht (eine Position)."
        )

    def read_position():
        base = position_field.address
        if position_field.via_pointer is not None:
            base = memory.follow_pointer(position_field.via_pointer) + position_field.offset
        return memory.read_vec3(base)

    # Frame-genau ankern: verglichen werden nur Positionen aus zwei direkt
    # aufeinanderfolgenden Frames. Wuerde man stattdessen zwei beliebige
    # Abfragen vergleichen, haenge der erkannte Frame davon ab, wie die
    # Abfragen zufaellig auf die Frames fallen - gemessen wurden so 6 Frames
    # Versatz zwischen zwei Laeufen desselben Ghosts.
    deadline = time.monotonic() + timeout
    previous_frame = memory.read_u32(frame_address)
    previous = read_position()
    while time.monotonic() < deadline:
        time.sleep(1.0 / 500.0)
        frame = memory.read_u32(frame_address)
        if frame == previous_frame:
            continue
        position = read_position()
        if frame == previous_frame + 1:
            moved = sum((c - p) ** 2 for c, p in zip(position, previous)) ** 0.5
            if moved >= threshold:
                return frame
        previous_frame = frame
        previous = position
    return None


def _record_run(args: argparse.Namespace) -> reference.RunResult:
    ghost = rkg.read(args.ghost)
    address_map = _load_verified_map(pathlib.Path(args.address_map))
    scored_map = addressmap.checksum_fields(address_map)

    frame_field = address_map.field("race_frame")
    timer_field = address_map.field("race_timer_ms")
    if frame_field is None or frame_field.address is None:
        raise addressmap.AddressMapError(
            "Die Adresskarte braucht ein Feld 'race_frame' mit Adresse: es ist "
            "die Zeitachse, unter der Referenz und Lauf verglichen werden."
        )

    samples: list[reference.Sample] = []
    measured_finish: int | None = None
    seen_frames: set[int] = set()
    torn = 0
    # Der Framezaehler ist nur waehrend eines Rennens gueltig. Danach steht an
    # derselben Stelle anderes: gemessen wurde ein Sprung auf 0x80452BE0, also
    # eine Gastadresse. Ein Zaehler springt nie um Millionen, deshalb gilt ein
    # zu grosser Schritt als "Rennen vorbei" und beendet die Aufzeichnung,
    # statt Muell in die Referenz zu schreiben.
    previous_frame: int | None = None
    implausible = 0
    stopped_early = False

    with guest_memory.GuestMemory(args.pid, target=args.target) as memory:
        if args.wait_for_motion:
            print(
                f"  Warte auf Bewegung (Schwelle {args.motion_threshold} "
                f"Einheiten je Frame) ..."
            )
            anchor = _wait_for_motion(
                memory, address_map, frame_field.address,
                args.motion_threshold, args.wait_timeout,
            )
            if anchor is None:
                raise guest_memory.GuestMemoryError(
                    "Keine Bewegung erkannt. Laeuft das Rennen, und faehrt das "
                    "Kart? Sonst --motion-threshold senken oder --no-wait-for-motion."
                )
            print(f"  Anker gesetzt bei Frame {anchor}")

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            # Zerrissene Stichproben verhindern: Der Frame wird vor und nach dem
            # Lesen geprueft. Springt er dazwischen, stammen die Werte aus zwei
            # verschiedenen Spielzustaenden - fuer einen exakten Vergleich waere
            # das Gift, also wird die Stichprobe verworfen statt geglaettet.
            before = memory.read_u32(frame_field.address)
            values = addressmap.sample(memory, scored_map)
            if timer_field is not None and timer_field.address is not None:
                measured_finish = memory.read_u32(timer_field.address)
            after = memory.read_u32(frame_field.address)
            if before != after:
                torn += 1
                continue
            if previous_frame is not None:
                delta = before - previous_frame
                if delta < 0 or delta > args.max_frame_jump:
                    implausible += 1
                    if implausible >= 3:
                        stopped_early = True
                        break
                    continue
            implausible = 0
            previous_frame = before

            if before not in seen_frames:
                seen_frames.add(before)
                per_field = None
                if args.per_field:
                    per_field = {
                        name: reference.checksum_positions([value])
                        for name, value in addressmap.sample_named(memory, scored_map)
                    }
                samples.append(
                    reference.Sample(
                        frame=before,
                        checksum=reference.checksum_positions(values),
                        fields=per_field,
                    )
                )
            if args.frames and len(samples) >= args.frames:
                break
            time.sleep(args.interval)

    if torn:
        print(f"  {torn} Stichprobe(n) verworfen, weil der Frame dazwischen sprang")
    if stopped_early:
        print(
            "  Aufzeichnung beendet: der Framezaehler wurde unplausibel "
            "(vermutlich Rennende - danach steht an der Adresse anderes)."
        )

    # Auf den Aufzeichnungsbeginn normieren. Der Framezaehler des Spiels laeuft
    # global durch (ueber Menues und fruehere Rennen hinweg), absolute Nummern
    # waeren zwischen zwei Laeufen also nie gleich.
    base = min((s.frame for s in samples), default=0)
    samples = [
        reference.Sample(frame=s.frame - base, checksum=s.checksum, fields=s.fields)
        for s in samples
    ]
    samples.sort(key=lambda s: s.frame)

    return reference.RunResult(
        ghost_name=ghost.source.name,
        track_id=ghost.track_id,
        lap_count=ghost.lap_count,
        expected_finish_ms=ghost.finish_time.total_milliseconds,
        measured_finish_ms=measured_finish,
        samples=tuple(samples),
        first_frame_absolute=base,
        host=reference.host_description(),
    )


def cmd_scan(args: argparse.Namespace) -> int:
    import scanrun

    return scanrun.run(args)


def cmd_pointerscan(args: argparse.Namespace) -> int:
    """Sucht einen statischen Zeiger auf eine Heap-Adresse.

    Heap-Adressen wechseln von Lauf zu Lauf. Eine Referenz, die Phasen und
    Architekturen ueberdauern soll, braucht deshalb einen Weg ueber einen
    statischen Zeiger statt einer nackten Heap-Adresse.
    """
    import scan

    target = int(args.target_address, 0)
    max_offset = int(args.max_offset, 0)
    start = guest_memory.MEM1_CACHED_BASE
    size = guest_memory.MEM1_SIZE
    if args.region == "mem2":
        start, size = guest_memory.MEM2_CACHED_BASE, guest_memory.MEM2_SIZE

    try:
        with guest_memory.GuestMemory(args.pid, target=args.target) as memory:
            print(f"Lese 0x{start:08X} + {size // (1024 * 1024)} MiB ...")
            snapshot = scan.read_region(memory, start, size)
            chains = scan.find_pointer_chains(
                snapshot, target, max_offset=max_offset, max_depth=args.depth
            )
    except guest_memory.GuestMemoryError as error:
        print(f"Lesen fehlgeschlagen: {error}", file=sys.stderr)
        return EXIT_BLOCKED

    usable = int(snapshot.chunk_valid.sum())
    print(f"Lesbar: {usable}/{len(snapshot.chunk_valid)} Bloecke")
    print(f"Ziel:   0x{target:08X}")
    print()

    if not chains:
        print("Keine Kette in den statischen Bereich gefunden.")
        print("  - Laeuft gerade ein Rennen? Ohne Kart-Objekt zeigt nichts dorthin.")
        print("  - Groesseren --max-offset probieren (Vorgabe 0x1000).")
        print("  - --depth 3 versuchen.")
        return EXIT_MISMATCH

    print(f"{len(chains)} Kette(n), beste zuerst:")
    for chain in chains[: args.show]:
        print(f"  {chain.describe()}")

    best = chains[0]
    if best.depth == 1:
        print()
        print("Fuer die Adresskarte:")
        print(f'  "via_pointer": "0x{best.root.at:08X}", "offset": {best.root.offset}')
    return EXIT_OK


def cmd_record(args: argparse.Namespace) -> int:
    try:
        result = _record_run(args)
    except (addressmap.AddressMapError, guest_memory.GuestMemoryError) as error:
        print(f"Aufzeichnung nicht moeglich:\n{error}", file=sys.stderr)
        return EXIT_BLOCKED

    path = reference.save(result, args.out)
    print(f"Referenz geschrieben: {path}")
    print(f"  Stichproben:   {len(result.samples)}")
    print(f"  Erwartet:      {result.expected_finish_ms} ms (aus dem Ghost-Header)")
    if result.measured_finish_ms is None:
        print("  Gemessen:      -- (Adresskarte hat kein Feld 'race_timer_ms')")
        print(
            "\nHinweis: Ohne Endzeit stuetzt sich der Vergleich allein auf die "
            "Pruefsummen je Frame. Als Paritaetskriterium traegt das, faengt "
            "aber nicht ab, dass ein Lauf frueher endet."
        )
    else:
        print(f"  Gemessen:      {result.measured_finish_ms} ms")
        if not result.finish_matches_ghost:
            print(
                "\nWARNUNG: Die gemessene Endzeit weicht vom Ghost ab. Als "
                "Referenz ist dieser Lauf nur brauchbar, wenn die Abweichung "
                "erklaert ist."
            )
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    expected = reference.load(args.reference)
    try:
        actual = _record_run(args)
    except (addressmap.AddressMapError, guest_memory.GuestMemoryError) as error:
        print(f"Pruefung nicht moeglich:\n{error}", file=sys.stderr)
        return EXIT_BLOCKED

    if args.save:
        reference.save(actual, args.save)
        print(f"Lauf gespeichert: {args.save}")

    result = reference.compare(expected, actual)
    print(result.report())
    if not result.passed:
        frame = reference.first_divergence_frame(expected, actual)
        if frame is not None:
            print(f"\nErste Abweichung bei Frame {frame} - dort ansetzen.")
    return EXIT_OK if result.passed else EXIT_MISMATCH


def cmd_compare(args: argparse.Namespace) -> int:
    expected = reference.load(args.reference)
    actual = reference.load(args.candidate)
    result = reference.compare(expected, actual)
    print(result.report())
    if not result.passed:
        frame = reference.first_divergence_frame(expected, actual)
        if frame is not None:
            print(f"\nErste Abweichung bei Frame {frame} - dort ansetzen.")
    return EXIT_OK if result.passed else EXIT_MISMATCH


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghostrun",
        description="Ghost-Harness fuer WiiCompiled: Physikparitaet nachweisen.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="Header einer Ghostdatei anzeigen")
    p.add_argument("ghost", help="Pfad zu einer .rkg-Datei (wird nur gelesen)")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("verify-layout", help="Parser gegen eine Ghost-Sammlung pruefen")
    p.add_argument("directory", nargs="+", help="Verzeichnis(se), rekursiv")
    p.set_defaults(func=cmd_verify_layout)

    def add_process_args(target_parser: argparse.ArgumentParser) -> None:
        target_parser.add_argument(
            "--pid", type=int, required=True, help="Prozess-ID des laufenden Spiels"
        )
        target_parser.add_argument(
            "--target",
            choices=sorted(guest_memory.FLAT_GUEST_BASE),
            default=None,
            help="Zielarchitektur fuer die flache Basis (Vorgabe: dieser Rechner)",
        )

    p = sub.add_parser("probe", help="Ist der Gastspeicher lesbar?")
    add_process_args(p)
    p.set_defaults(func=cmd_probe)

    def add_map_arg(target_parser: argparse.ArgumentParser) -> None:
        target_parser.add_argument(
            "--address-map",
            default=str(DEFAULT_ADDRESS_MAP),
            help=f"Adresskarte (Vorgabe: {DEFAULT_ADDRESS_MAP.name})",
        )

    p = sub.add_parser("sample", help="Adresskarte einmalig abtasten")
    add_process_args(p)
    add_map_arg(p)
    p.set_defaults(func=cmd_sample)

    def add_run_args(target_parser: argparse.ArgumentParser) -> None:
        add_process_args(target_parser)
        add_map_arg(target_parser)
        target_parser.add_argument(
            "--ghost", required=True, help="Die abgespielte .rkg-Datei (nur gelesen)"
        )
        target_parser.add_argument(
            "--interval", type=float, default=1.0 / 60.0,
            help="Abtastabstand in Sekunden (Vorgabe: ein Frame bei 60 fps)"
        )
        target_parser.add_argument(
            "--frames", type=int, default=0,
            help="Nach so vielen Stichproben aufhoeren (0 = bis zum Timeout)"
        )
        target_parser.add_argument(
            "--timeout", type=float, default=600.0,
            help="Obergrenze in Sekunden (Vorgabe: 600)"
        )
        target_parser.add_argument(
            "--per-field", action="store_true",
            help="Zusaetzlich je Feld eine Pruefsumme ablegen. Nur zur "
                 "Fehlersuche: damit laesst sich zeigen, welches Feld wackelt."
        )
        target_parser.add_argument(
            "--wait-for-motion", action=argparse.BooleanOptionalAction, default=True,
            help="Erst ab der ersten echten Bewegung aufzeichnen. Ohne diesen "
                 "Anker sind zwei Laeufe nicht vergleichbar (Vorgabe: an)."
        )
        target_parser.add_argument(
            "--motion-threshold", type=float, default=1.0,
            help="Ab welcher Verschiebung je Frame es als Bewegung gilt "
                 "(Vorgabe: 1.0). Muss ueber dem Zittern im Stand liegen."
        )
        target_parser.add_argument(
            "--wait-timeout", type=float, default=120.0,
            help="Wie lange auf Bewegung gewartet wird (Vorgabe: 120 s)"
        )
        target_parser.add_argument(
            "--max-frame-jump", type=int, default=600,
            help="Groesster plausibler Framesprung zwischen zwei Stichproben "
                 "(Vorgabe: 600 = 10 s). Darueber gilt das Rennen als beendet."
        )

    p = sub.add_parser(
        "scan",
        help="Adressen empirisch suchen (zwei Phasen: stehen, dann fahren)",
    )
    add_process_args(p)
    p.add_argument("--region", choices=("mem1", "mem2"), default="mem1",
                   help="Zu durchsuchende Region (Vorgabe: mem1)")
    p.add_argument("--out", default=None,
                   help="Vorschlag als Adresskarte schreiben (bleibt unverifiziert)")
    p.add_argument("--lead-in", type=int, default=5,
                   help="Countdown vor jeder Phase in Sekunden")
    p.add_argument("--standstill-samples", type=int, default=6)
    p.add_argument("--standstill-interval", type=float, default=0.25)
    p.add_argument("--motion-samples", type=int, default=12)
    p.add_argument("--motion-interval", type=float, default=0.20)
    p.add_argument("--smoothness", type=float, default=25.0,
                   help="Obergrenze fuer Sprunghaftigkeit (kleiner = strenger)")
    p.add_argument("--show", type=int, default=10, help="Wie viele Treffer anzeigen")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser(
        "pointerscan",
        help="Statischen Zeiger auf eine Heap-Adresse suchen",
    )
    add_process_args(p)
    p.add_argument("--address", dest="target_address", required=True,
                   help="Die Heap-Gastadresse, zu der ein Zeiger gesucht wird")
    p.add_argument("--region", choices=("mem1", "mem2"), default="mem1")
    p.add_argument("--max-offset", default="0x1000",
                   help="Wie weit innerhalb eines Objekts das Feld liegen darf")
    p.add_argument("--depth", type=int, default=2, help="Maximale Kettentiefe")
    p.add_argument("--show", type=int, default=15)
    p.set_defaults(func=cmd_pointerscan)

    p = sub.add_parser("record", help="Referenzlauf aufzeichnen")
    add_run_args(p)
    p.add_argument("--out", required=True, help="Zieldatei der Referenz")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("check", help="Lauf gegen eine Referenz pruefen")
    add_run_args(p)
    p.add_argument("--reference", required=True, help="Referenzdatei")
    p.add_argument("--save", default=None, help="Den Lauf zusaetzlich hier ablegen")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("compare", help="Zwei gespeicherte Laeufe vergleichen")
    p.add_argument("reference")
    p.add_argument("candidate")
    p.set_defaults(func=cmd_compare)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except rkg.RkgError as error:
        print(f"Ghostdatei nicht lesbar: {error}", file=sys.stderr)
        return EXIT_USAGE
    except FileNotFoundError as error:
        print(f"Datei nicht gefunden: {error}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
