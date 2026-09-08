"""Tests des Ghost-Harness.

Laufen ohne Spieldaten, ohne laufendes Spiel und ohne Netzwerk. Alle
Ghost-Header sind hier synthetisch erzeugt: das Harness soll auch dann pruefbar
bleiben, wenn auf dem Rechner keine einzige echte Ghostdatei liegt.

    python -m unittest discover -s harness/ghost/tests -v
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import sys
import tempfile
import unittest

HARNESS = pathlib.Path(__file__).resolve().parent.parent
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import addressmap  # noqa: E402
import guest_memory  # noqa: E402
import reference  # noqa: E402
import rkg  # noqa: E402


# --------------------------------------------------------------------------
# Synthetische Ghost-Header
# --------------------------------------------------------------------------


def pack_time_24(minutes: int, seconds: int, milliseconds: int) -> bytes:
    raw = (minutes & 0x7F) << 17 | (seconds & 0x7F) << 10 | (milliseconds & 0x3FF)
    return raw.to_bytes(3, "big")


def make_header(
    *,
    minutes: int = 1,
    seconds: int = 59,
    milliseconds: int = 402,
    track_id: int = 4,
    vehicle_id: int = 21,
    character_id: int = 9,
    year: int = 26,
    month: int = 7,
    day: int = 20,
    controller_id: int = 1,
    compressed: bool = True,
    ghost_type: int = 3,
    drift_type: int = 1,
    input_length: int = 1234,
    laps: list[tuple[int, int, int]] | None = None,
    magic: bytes = rkg.MAGIC,
) -> bytes:
    """Baut einen Header, der der dokumentierten Feldbelegung entspricht."""
    if laps is None:
        laps = [(0, 39, 800), (0, 39, 801), (0, 39, 801)]

    data = bytearray(rkg.HEADER_SIZE)
    data[0:4] = magic

    word_time = (
        (minutes & 0x7F) << 25
        | (seconds & 0x7F) << 18
        | (milliseconds & 0x3FF) << 8
        | (track_id & 0x3F) << 2
    )
    data[4:8] = word_time.to_bytes(4, "big")

    word_meta = (
        (vehicle_id & 0x3F) << 26
        | (character_id & 0x3F) << 20
        | (year & 0x7F) << 13
        | (month & 0xF) << 9
        | (day & 0x1F) << 4
        | (controller_id & 0xF)
    )
    data[8:12] = word_meta.to_bytes(4, "big")

    half_flags = (
        (1 if compressed else 0) << 11
        | (ghost_type & 0x7F) << 2
        | (drift_type & 1) << 1
    )
    data[12:14] = half_flags.to_bytes(2, "big")
    data[14:16] = input_length.to_bytes(2, "big")
    data[16] = len(laps)

    for index, lap in enumerate(laps[: rkg.MAX_LAPS]):
        offset = rkg.LAP_TABLE_OFFSET + index * rkg.LAP_ENTRY_SIZE
        data[offset : offset + 3] = pack_time_24(*lap)

    return bytes(data)


class TestRkgParsing(unittest.TestCase):
    def test_reads_every_documented_field(self) -> None:
        ghost = rkg.parse_header(make_header())
        self.assertEqual(ghost.finish_time.minutes, 1)
        self.assertEqual(ghost.finish_time.seconds, 59)
        self.assertEqual(ghost.finish_time.milliseconds, 402)
        self.assertEqual(ghost.track_id, 4)
        self.assertEqual(ghost.vehicle_id, 21)
        self.assertEqual(ghost.character_id, 9)
        self.assertEqual(ghost.year, 2026)
        self.assertEqual(ghost.month, 7)
        self.assertEqual(ghost.day, 20)
        self.assertEqual(ghost.controller_id, 1)
        self.assertTrue(ghost.compressed)
        self.assertEqual(ghost.ghost_type, 3)
        self.assertEqual(ghost.drift_type, 1)
        self.assertEqual(ghost.input_data_length, 1234)
        self.assertEqual(ghost.lap_count, 3)

    def test_total_milliseconds(self) -> None:
        ghost = rkg.parse_header(make_header())
        self.assertEqual(ghost.finish_time.total_milliseconds, 119402)

    def test_lap_times_are_trimmed_to_lap_count(self) -> None:
        ghost = rkg.parse_header(make_header(laps=[(0, 30, 0), (0, 31, 0)]))
        self.assertEqual(len(ghost.lap_times), 2)

    def test_bitfields_do_not_bleed_into_each_other(self) -> None:
        # Maximalwerte in jedem Feld: ein Ueberlauf wuerde die Nachbarn treffen.
        ghost = rkg.parse_header(
            make_header(
                minutes=0x7F, seconds=0x7F, milliseconds=0x3FF, track_id=0x3F,
                vehicle_id=0x3F, character_id=0x3F, year=0x7F, month=0xF,
                day=0x1F, controller_id=0xF,
            )
        )
        self.assertEqual(ghost.finish_time.minutes, 0x7F)
        self.assertEqual(ghost.finish_time.seconds, 0x7F)
        self.assertEqual(ghost.finish_time.milliseconds, 0x3FF)
        self.assertEqual(ghost.track_id, 0x3F)
        self.assertEqual(ghost.vehicle_id, 0x3F)
        self.assertEqual(ghost.character_id, 0x3F)
        self.assertEqual(ghost.day, 0x1F)
        self.assertEqual(ghost.controller_id, 0xF)

    def test_rejects_wrong_magic(self) -> None:
        with self.assertRaises(rkg.RkgError):
            rkg.parse_header(make_header(magic=b"XXXX"))

    def test_rejects_short_header(self) -> None:
        with self.assertRaises(rkg.RkgError):
            rkg.parse_header(make_header()[:0x40])

    def test_reads_from_disk_without_copying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "synthetic.rkg"
            # Header plus Fuellbytes: read() darf nur den Header anfassen.
            path.write_bytes(make_header() + b"\xAB" * 4096)
            ghost = rkg.read(path)
            self.assertEqual(ghost.track_id, 4)
            self.assertEqual(ghost.source.name, "synthetic.rkg")


class TestLayoutValidation(unittest.TestCase):
    def test_consistent_header_passes(self) -> None:
        check = rkg.validate_layout(rkg.parse_header(make_header()))
        self.assertTrue(check.ok, check.problems)

    def test_lap_sum_mismatch_is_reported(self) -> None:
        ghost = rkg.parse_header(make_header(laps=[(0, 10, 0), (0, 10, 0), (0, 10, 0)]))
        check = rkg.validate_layout(ghost)
        self.assertFalse(check.ok)
        self.assertTrue(any("Rundenzeiten" in p for p in check.problems))

    def test_impossible_lap_count_is_reported(self) -> None:
        header = bytearray(make_header())
        header[16] = 7
        check = rkg.validate_layout(rkg.parse_header(bytes(header)))
        self.assertFalse(check.ok)
        self.assertTrue(any("Rundenzahl" in p for p in check.problems))

    def test_impossible_seconds_are_reported(self) -> None:
        ghost = rkg.parse_header(make_header(seconds=90))
        check = rkg.validate_layout(ghost)
        self.assertFalse(check.ok)


class TestGuestMemoryArithmetic(unittest.TestCase):
    """Die Adressrechnung ist ohne laufendes Spiel pruefbar - und muss es sein,
    weil sie die Grundlage jedes Messwerts ist."""

    def test_bases_match_the_runtime_header(self) -> None:
        # runtime/include/guest_flat_memory.h:19-38
        self.assertEqual(guest_memory.FLAT_GUEST_BASE["x86_64"], 0x0000_1000_0000_0000)
        self.assertEqual(
            guest_memory.FLAT_GUEST_BASE["aarch64-apple"], 0x0000_0080_0000_0000
        )
        self.assertEqual(guest_memory.FLAT_GUEST_BASE["aarch64"], 0x0000_0010_0000_0000)

    def test_guest_space_is_four_gigabytes(self) -> None:
        self.assertEqual(guest_memory.GUEST_SPACE_SIZE, 0x1_0000_0000)

    def test_host_address_is_base_plus_guest(self) -> None:
        self.assertEqual(
            guest_memory.host_address(0x8000_0000, "x86_64"),
            0x0000_1000_0000_0000 + 0x8000_0000,
        )
        self.assertEqual(
            guest_memory.host_address(0x8000_0000, "aarch64"),
            0x0000_0010_0000_0000 + 0x8000_0000,
        )

    def test_same_guest_address_differs_per_architecture(self) -> None:
        x86 = guest_memory.host_address(0x809B_D730, "x86_64")
        arm = guest_memory.host_address(0x809B_D730, "aarch64")
        self.assertNotEqual(x86, arm)
        self.assertEqual(x86 - arm, 0x0000_1000_0000_0000 - 0x0000_0010_0000_0000)

    def test_address_outside_guest_space_is_rejected(self) -> None:
        with self.assertRaises(guest_memory.GuestMemoryError):
            guest_memory.host_address(0x1_0000_0000, "x86_64")
        with self.assertRaises(guest_memory.GuestMemoryError):
            guest_memory.host_address(-1, "x86_64")

    def test_unknown_target_is_rejected(self) -> None:
        with self.assertRaises(guest_memory.GuestMemoryError):
            guest_memory.flat_base_for("riscv64")

    def test_wii_memory_map_matches_runtime_header(self) -> None:
        # runtime/include/memory.h:14-28
        self.assertEqual(guest_memory.MEM1_CACHED_BASE, 0x8000_0000)
        self.assertEqual(guest_memory.MEM1_SIZE, 24 * 1024 * 1024)
        self.assertEqual(guest_memory.MEM2_CACHED_BASE, 0x9000_0000)
        self.assertEqual(guest_memory.MEM2_SIZE, 128 * 1024 * 1024)

    def test_mapped_ram_detection(self) -> None:
        self.assertTrue(guest_memory.is_mapped_ram(0x8000_0000, 4))
        self.assertTrue(guest_memory.is_mapped_ram(0x9000_0000, 4))
        self.assertFalse(guest_memory.is_mapped_ram(0x0000_1000, 4))
        # Ein Byte hinter dem Ende von MEM1 gehoert nicht mehr dazu.
        self.assertFalse(
            guest_memory.is_mapped_ram(0x8000_0000 + guest_memory.MEM1_SIZE, 1)
        )


class TestAddressMap(unittest.TestCase):
    def _write(self, payload: dict) -> pathlib.Path:
        directory = pathlib.Path(tempfile.mkdtemp())
        path = directory / "map.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_unverified_map_blocks_recording(self) -> None:
        path = self._write(
            {
                "schema_version": 1,
                "game_id": "RMCP01",
                "verified": False,
                "fields": [{"name": "x", "address": "0x80000000", "kind": "u32"}],
            }
        )
        with self.assertRaises(addressmap.AddressMapError):
            addressmap.load(path).require_verified()

    def test_verified_but_empty_map_is_rejected(self) -> None:
        path = self._write(
            {"schema_version": 1, "game_id": "RMCP01", "verified": True, "fields": []}
        )
        with self.assertRaises(addressmap.AddressMapError):
            addressmap.load(path).require_verified()

    def test_verified_map_with_fields_is_accepted(self) -> None:
        path = self._write(
            {
                "schema_version": 1,
                "game_id": "RMCP01",
                "verified": True,
                "fields": [
                    {"name": "pos", "address": "0x809BD730", "kind": "vec3"},
                    {"name": "frame", "address": 0x80000000, "kind": "u32"},
                ],
            }
        )
        loaded = addressmap.load(path)
        loaded.require_verified()
        self.assertEqual(len(loaded.fields), 2)
        self.assertEqual(loaded.field("pos").address, 0x809BD730)
        self.assertEqual(loaded.field("frame").address, 0x80000000)
        self.assertIsNone(loaded.field("gibtsnicht"))

    def test_empty_address_is_rejected(self) -> None:
        path = self._write(
            {
                "schema_version": 1,
                "verified": True,
                "fields": [{"name": "x", "address": "", "kind": "u32"}],
            }
        )
        with self.assertRaises(addressmap.AddressMapError):
            addressmap.load(path)

    def test_unknown_kind_is_rejected(self) -> None:
        path = self._write(
            {
                "schema_version": 1,
                "verified": True,
                "fields": [{"name": "x", "address": "0x80000000", "kind": "float128"}],
            }
        )
        with self.assertRaises(addressmap.AddressMapError):
            addressmap.load(path)

    def test_wrong_schema_version_is_rejected(self) -> None:
        path = self._write({"schema_version": 99, "verified": True, "fields": []})
        with self.assertRaises(addressmap.AddressMapError):
            addressmap.load(path)


class TestChecksum(unittest.TestCase):
    def test_is_stable(self) -> None:
        values = [1.5, 2.5, 3]
        self.assertEqual(
            reference.checksum_positions(values),
            reference.checksum_positions(list(values)),
        )

    def test_last_bit_difference_changes_the_checksum(self) -> None:
        """Der Kernpunkt: eine Abweichung im letzten Bit darf nicht verschwinden."""
        import struct

        base = 1.0
        nudged = struct.unpack(">d", struct.pack(">Q", struct.unpack(">Q", struct.pack(">d", base))[0] + 1))[0]
        self.assertNotEqual(base, nudged)
        self.assertNotEqual(
            reference.checksum_positions([base]),
            reference.checksum_positions([nudged]),
        )

    def test_order_matters(self) -> None:
        self.assertNotEqual(
            reference.checksum_positions([1.0, 2.0]),
            reference.checksum_positions([2.0, 1.0]),
        )

    def test_int_and_float_of_same_value_differ(self) -> None:
        self.assertNotEqual(
            reference.checksum_positions([1]),
            reference.checksum_positions([1.0]),
        )


def make_run(
    *,
    name: str = "0_150.rkg",
    track: int = 4,
    laps: int = 3,
    expected: int = 119402,
    measured: int | None = 119402,
    samples: list[tuple[int, str]] | None = None,
) -> reference.RunResult:
    if samples is None:
        samples = [(0, "aa"), (1, "bb"), (2, "cc")]
    return reference.RunResult(
        ghost_name=name,
        track_id=track,
        lap_count=laps,
        expected_finish_ms=expected,
        measured_finish_ms=measured,
        samples=tuple(reference.Sample(frame=f, checksum=c) for f, c in samples),
        host=reference.host_description(),
    )


class TestComparison(unittest.TestCase):
    def test_identical_runs_pass(self) -> None:
        result = reference.compare(make_run(), make_run())
        self.assertTrue(result.passed, result.differences)
        self.assertEqual(result.checked_samples, 3)

    def test_differing_finish_time_fails(self) -> None:
        result = reference.compare(make_run(), make_run(measured=119403))
        self.assertFalse(result.passed)
        self.assertTrue(any("Endzeit" in d for d in result.differences))

    def test_single_differing_checksum_fails(self) -> None:
        other = make_run(samples=[(0, "aa"), (1, "XX"), (2, "cc")])
        result = reference.compare(make_run(), other)
        self.assertFalse(result.passed)
        self.assertTrue(any("Stichproben weichen ab" in d for d in result.differences))

    def test_first_divergence_frame_is_the_earliest(self) -> None:
        other = make_run(samples=[(0, "aa"), (1, "XX"), (2, "YY")])
        self.assertEqual(reference.first_divergence_frame(make_run(), other), 1)

    def test_no_divergence_returns_none(self) -> None:
        self.assertIsNone(reference.first_divergence_frame(make_run(), make_run()))

    def test_missing_samples_are_reported(self) -> None:
        other = make_run(samples=[(0, "aa")])
        result = reference.compare(make_run(), other)
        self.assertFalse(result.passed)
        self.assertTrue(any("fehlen" in d for d in result.differences))

    def test_different_track_fails(self) -> None:
        result = reference.compare(make_run(), make_run(track=5))
        self.assertFalse(result.passed)
        self.assertTrue(any("Strecke" in d for d in result.differences))

    def test_comparison_is_exact_not_tolerant(self) -> None:
        """Kein Spielraum: eine Millisekunde Abweichung ist ein Fehlschlag."""
        result = reference.compare(make_run(measured=119402), make_run(measured=119401))
        self.assertFalse(result.passed)


class TestFrameNormalisation(unittest.TestCase):
    """Frames sind relativ zum Aufzeichnungsbeginn, sonst waere kein Vergleich
    zwischen zwei Laeufen moeglich."""

    def test_absolute_frame_offset_does_not_affect_comparison(self) -> None:
        a = make_run(samples=[(0, "aa"), (1, "bb"), (2, "cc")])
        b = make_run(samples=[(0, "aa"), (1, "bb"), (2, "cc")])
        b = dataclasses.replace(b, first_frame_absolute=999999)
        self.assertTrue(reference.compare(a, b).passed)

    def test_first_frame_absolute_survives_a_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ref.json"
            original = dataclasses.replace(make_run(), first_frame_absolute=157591)
            reference.save(original, path)
            self.assertEqual(reference.load(path).first_frame_absolute, 157591)


class TestReferenceRoundTrip(unittest.TestCase):
    def test_save_and_load_preserves_everything(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ref.json"
            original = make_run()
            reference.save(original, path)
            loaded = reference.load(path)
            self.assertEqual(loaded.ghost_name, original.ghost_name)
            self.assertEqual(loaded.track_id, original.track_id)
            self.assertEqual(loaded.measured_finish_ms, original.measured_finish_ms)
            self.assertEqual(len(loaded.samples), len(original.samples))
            self.assertTrue(reference.compare(original, loaded).passed)

    def test_saved_reference_records_when_and_where(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ref.json"
            reference.save(make_run(), path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(payload["recorded_at"])
            self.assertIn("machine", payload["host"])

    def test_wrong_schema_version_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            reference.RunResult.from_json({"schema_version": 99})

    def test_reference_contains_no_ghost_payload(self) -> None:
        """Sicherung gegen versehentliches Einbetten von Spieldaten."""
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ref.json"
            reference.save(make_run(), path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            allowed = {
                "schema_version", "ghost_name", "track_id", "lap_count",
                "expected_finish_ms", "measured_finish_ms", "samples",
                "first_frame_absolute", "host", "recorded_at",
            }
            self.assertEqual(set(payload), allowed)
            for sample in payload["samples"]:
                self.assertEqual(set(sample), {"frame", "checksum"})


if __name__ == "__main__":
    unittest.main()


class TestPointerFields(unittest.TestCase):
    """Felder hinter einem Zeiger brauchen keine eigene Adresse."""

    def _write(self, payload: dict) -> pathlib.Path:
        path = pathlib.Path(tempfile.mkdtemp()) / "map.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_verified_map_accepts_pointer_field_without_address(self) -> None:
        path = self._write({
            "schema_version": 1, "game_id": "RMCP01", "verified": True,
            "fields": [{"name": "pos", "address": "", "via_pointer": "0x80398030",
                        "offset": 88, "kind": "vec3"}],
        })
        loaded = addressmap.load(path)
        loaded.require_verified()
        field = loaded.field("pos")
        self.assertIsNone(field.address)
        self.assertEqual(field.via_pointer, 0x80398030)
        self.assertEqual(field.offset, 88)

    def test_field_without_address_and_without_pointer_is_still_rejected(self) -> None:
        path = self._write({
            "schema_version": 1, "verified": True,
            "fields": [{"name": "x", "address": "", "kind": "u32"}],
        })
        with self.assertRaises(addressmap.AddressMapError):
            addressmap.load(path).require_verified()

    def test_shipped_map_loads_and_is_usable(self) -> None:
        loaded = addressmap.load(HARNESS / "addresses" / "rmcp01-pal.json")
        loaded.require_verified()
        self.assertEqual(loaded.field("race_frame").address, 0x8034750C)
        self.assertEqual(loaded.field("kart_point_0").via_pointer, 0x80398030)
