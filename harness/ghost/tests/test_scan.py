"""Tests der Scanner-Analyse.

Der Speicher ist hier synthetisch: eine gebaute Region, in die bekannte Felder
an bekannte Adressen gelegt werden. Damit ist pruefbar, ob der Scanner genau
die findet - ohne laufendes Spiel, ohne Spieldaten.
"""

from __future__ import annotations

import pathlib
import struct
import sys
import unittest

HARNESS = pathlib.Path(__file__).resolve().parent.parent
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import numpy as np  # noqa: E402

import scan  # noqa: E402

GUEST_START = 0x8000_0000
REGION_SIZE = scan.CHUNK_SIZE * 4  # 256 KiB reichen fuer die Tests


class FakeMemory:
    """Ein Speicher, der einzelne Bloecke absichtlich verweigert.

    Bildet das Verhalten des Gast-Views nach: geschuetzte Seiten sind normal
    und duerfen den Scanner nicht aus dem Tritt bringen.
    """

    def __init__(self, data: bytes, blocked_chunks: set[int] | None = None) -> None:
        self.data = bytearray(data)
        self.blocked = blocked_chunks or set()

    def read_bytes(self, guest_address: int, size: int) -> bytes:
        offset = guest_address - GUEST_START
        if (offset // scan.CHUNK_SIZE) in self.blocked:
            raise RuntimeError("Seite geschuetzt")
        return bytes(self.data[offset : offset + size])


def blank_region() -> bytearray:
    # Ein Muster ungleich null, damit "unveraendert" nicht mit "leer" zusammenfaellt.
    return bytearray(b"\x11\x22\x33\x44" * (REGION_SIZE // 4))


def put_vec3(region: bytearray, offset: int, vector) -> None:
    region[offset : offset + 12] = struct.pack(">fff", *vector)


def put_u32(region: bytearray, offset: int, value: int) -> None:
    region[offset : offset + 4] = struct.pack(">I", value & 0xFFFFFFFF)


def snapshot_from(region: bytearray, timestamp: float, blocked=None) -> scan.Snapshot:
    chunk_count = REGION_SIZE // scan.CHUNK_SIZE
    valid = np.ones(chunk_count, dtype=bool)
    for index in blocked or ():
        valid[index] = False
    return scan.Snapshot(
        guest_start=GUEST_START,
        data=bytes(region),
        chunk_valid=valid,
        timestamp=timestamp,
    )


POSITION_OFFSET = 0x1000
VELOCITY_OFFSET = 0x1020   # nah dran, wie in einer Spielstruktur
FRAME_OFFSET = 0x2000
TIMER_OFFSET = 0x2004
DECOY_OFFSET = 0x3000      # springt wild - darf nicht als Position gelten


def build_phases(
    *,
    standstill_count: int = 6,
    motion_count: int = 12,
    standstill_dt: float = 0.25,
    motion_dt: float = 0.20,
):
    """Baut zwei Phasen mit einem stehenden und einem fahrenden Kart."""
    standstill: list[scan.Snapshot] = []
    motion: list[scan.Snapshot] = []

    position = [1200.5, 300.25, -4500.75]
    frame = 1000
    timer = 5000
    now = 100.0

    for _ in range(standstill_count):
        region = blank_region()
        put_vec3(region, POSITION_OFFSET, position)      # steht still
        put_vec3(region, VELOCITY_OFFSET, (0.0, 0.0, 0.0))
        put_u32(region, FRAME_OFFSET, frame)
        put_u32(region, TIMER_OFFSET, timer)
        put_vec3(region, DECOY_OFFSET, (7.0, 7.0, 7.0))  # im Stand auch konstant
        standstill.append(snapshot_from(region, now))
        frame += int(round(60 * standstill_dt))
        timer += int(round(1000 * standstill_dt))
        now += standstill_dt

    velocity = (12.0, 0.5, -3.0)
    decoy_value = 7.0
    for index in range(motion_count):
        position = [
            position[0] + velocity[0] * motion_dt,
            position[1] + velocity[1] * motion_dt,
            position[2] + velocity[2] * motion_dt,
        ]
        # Der Koeder springt sprunghaft - genau das soll aussortiert werden.
        decoy_value = decoy_value * (100.0 if index % 2 == 0 else 0.01)
        region = blank_region()
        put_vec3(region, POSITION_OFFSET, position)
        put_vec3(region, VELOCITY_OFFSET, velocity)
        put_u32(region, FRAME_OFFSET, frame)
        put_u32(region, TIMER_OFFSET, timer)
        put_vec3(region, DECOY_OFFSET, (decoy_value, decoy_value, decoy_value))
        motion.append(snapshot_from(region, now))
        frame += int(round(60 * motion_dt))
        timer += int(round(1000 * motion_dt))
        now += motion_dt

    return standstill, motion


class TestRegionReading(unittest.TestCase):
    def test_reads_whole_region(self) -> None:
        memory = FakeMemory(bytes(blank_region()))
        snapshot = scan.read_region(memory, GUEST_START, REGION_SIZE)
        self.assertTrue(snapshot.chunk_valid.all())
        self.assertEqual(len(snapshot.data), REGION_SIZE)

    def test_protected_pages_do_not_abort_the_scan(self) -> None:
        memory = FakeMemory(bytes(blank_region()), blocked_chunks={1})
        snapshot = scan.read_region(memory, GUEST_START, REGION_SIZE)
        self.assertFalse(snapshot.chunk_valid[1])
        self.assertTrue(snapshot.chunk_valid[0])
        self.assertEqual(int(snapshot.chunk_valid.sum()), 3)

    def test_invalid_chunks_are_excluded_from_elements(self) -> None:
        memory = FakeMemory(bytes(blank_region()), blocked_chunks={0})
        snapshot = scan.read_region(memory, GUEST_START, REGION_SIZE)
        valid = snapshot.element_valid()
        self.assertFalse(valid[0])
        self.assertTrue(valid[scan.CHUNK_SIZE // 4])


class TestCounters(unittest.TestCase):
    def test_finds_frame_counter_and_millisecond_clock(self) -> None:
        standstill, motion = build_phases()
        found = scan.find_counters(standstill + motion)
        addresses = {(c.guest_address, c.kind) for c in found}
        self.assertIn((GUEST_START + FRAME_OFFSET, "frame"), addresses)
        self.assertIn((GUEST_START + TIMER_OFFSET, "millisecond"), addresses)

    def test_rates_are_about_right(self) -> None:
        standstill, motion = build_phases()
        found = scan.find_counters(standstill + motion)
        frame = next(c for c in found if c.guest_address == GUEST_START + FRAME_OFFSET)
        timer = next(c for c in found if c.guest_address == GUEST_START + TIMER_OFFSET)
        self.assertAlmostEqual(frame.rate_per_second, 60.0, delta=8.0)
        self.assertAlmostEqual(timer.rate_per_second, 1000.0, delta=80.0)

    def test_constant_values_are_not_counters(self) -> None:
        standstill, motion = build_phases()
        found = scan.find_counters(standstill + motion)
        # Das Fuellmuster 0x11223344 aendert sich nie.
        self.assertFalse(any(c.guest_address == GUEST_START + 0 for c in found))

    def test_too_few_snapshots_yields_nothing(self) -> None:
        standstill, _ = build_phases()
        self.assertEqual(scan.find_counters(standstill[:2]), [])


class TestVec3Detection(unittest.TestCase):
    def setUp(self) -> None:
        self.standstill, self.motion = build_phases()
        self.found = scan.find_vec3_fields(self.standstill, self.motion)

    def test_finds_the_position(self) -> None:
        positions = scan.rank(self.found, "position")
        addresses = [c.guest_address for c in positions]
        self.assertIn(GUEST_START + POSITION_OFFSET, addresses)

    def test_finds_the_velocity(self) -> None:
        velocities = scan.rank(self.found, "velocity")
        addresses = [c.guest_address for c in velocities]
        self.assertIn(GUEST_START + VELOCITY_OFFSET, addresses)

    def test_position_and_velocity_are_told_apart(self) -> None:
        by_address = {c.guest_address: c for c in self.found}
        self.assertEqual(by_address[GUEST_START + POSITION_OFFSET].role, "position")
        self.assertEqual(by_address[GUEST_START + VELOCITY_OFFSET].role, "velocity")

    def test_erratic_field_is_rejected(self) -> None:
        """Ein Wert, der wild springt, ist keine Position."""
        addresses = [c.guest_address for c in self.found]
        self.assertNotIn(GUEST_START + DECOY_OFFSET, addresses)

    def test_position_ranks_first(self) -> None:
        positions = scan.rank(self.found, "position")
        self.assertEqual(positions[0].guest_address, GUEST_START + POSITION_OFFSET)

    def test_pairs_position_with_nearby_velocity(self) -> None:
        pairs = scan.pair_velocity_with_position(
            scan.rank(self.found, "position"), scan.rank(self.found, "velocity")
        )
        self.assertTrue(pairs)
        position, velocity = pairs[0]
        self.assertEqual(position.guest_address, GUEST_START + POSITION_OFFSET)
        self.assertEqual(velocity.guest_address, GUEST_START + VELOCITY_OFFSET)

    def test_field_that_moves_while_standing_is_rejected(self) -> None:
        """Bewegt sich etwas schon im Stand, ist es nicht die Kartposition."""
        standstill, motion = build_phases()
        moving = []
        for index, snapshot in enumerate(standstill):
            region = bytearray(snapshot.data)
            put_vec3(region, POSITION_OFFSET, (1200.5 + index, 300.25, -4500.75))
            moving.append(snapshot_from(region, snapshot.timestamp))
        found = scan.find_vec3_fields(moving, motion)
        addresses = [c.guest_address for c in found]
        self.assertNotIn(GUEST_START + POSITION_OFFSET, addresses)

    def test_field_frozen_during_motion_is_rejected(self) -> None:
        """Bewegt sich beim Fahren nichts, ist es keine Position."""
        standstill, _ = build_phases()
        frozen_motion = [
            snapshot_from(bytearray(standstill[0].data), 200.0 + index * 0.2)
            for index in range(12)
        ]
        found = scan.find_vec3_fields(standstill, frozen_motion)
        addresses = [c.guest_address for c in found]
        self.assertNotIn(GUEST_START + POSITION_OFFSET, addresses)

    def test_too_few_snapshots_yields_nothing(self) -> None:
        standstill, motion = build_phases()
        self.assertEqual(scan.find_vec3_fields(standstill[:1], motion), [])
        self.assertEqual(scan.find_vec3_fields(standstill, motion[:2]), [])


class TestPlausibility(unittest.TestCase):
    def test_zero_vector_is_not_a_position(self) -> None:
        values = np.array([0.0, 0.0, 0.0, 0.0], dtype=">f4")
        self.assertFalse(scan.plausible_vec3_mask(values)[0])

    def test_huge_values_are_rejected(self) -> None:
        values = np.array([1e30, 1e30, 1e30, 0.0], dtype=">f4")
        self.assertFalse(scan.plausible_vec3_mask(values)[0])

    def test_nan_is_rejected(self) -> None:
        values = np.array([float("nan"), 1.0, 1.0, 0.0], dtype=">f4")
        self.assertFalse(scan.plausible_vec3_mask(values)[0])

    def test_typical_world_coordinate_is_accepted(self) -> None:
        values = np.array([1200.5, 300.25, -4500.75, 0.0], dtype=">f4")
        self.assertTrue(scan.plausible_vec3_mask(values)[0])


if __name__ == "__main__":
    unittest.main()


class TestPointerSearch(unittest.TestCase):
    """Zeigersuche gegen einen gebauten Speicher mit bekannter Kette."""

    STATIC_POINTER = 0x0100        # liegt bei 0x80000100 -> statisch
    OBJECT_BASE = 0x80000000 + 0x1000
    FIELD_OFFSET = 0x68

    def _region_with_chain(self) -> bytearray:
        region = blank_region()
        # Ein statischer Zeiger zeigt auf den Objektanfang.
        put_u32(region, self.STATIC_POINTER, self.OBJECT_BASE)
        return region

    def test_finds_direct_pointer(self) -> None:
        snapshot = snapshot_from(self._region_with_chain(), 0.0)
        hops = scan.find_pointers_to(snapshot, self.OBJECT_BASE, max_offset=0)
        addresses = [h.at for h in hops]
        self.assertIn(GUEST_START + self.STATIC_POINTER, addresses)

    def test_finds_pointer_to_object_start_with_field_offset(self) -> None:
        """Der Zeiger zeigt auf den Objektanfang, gesucht ist ein Feld darin."""
        snapshot = snapshot_from(self._region_with_chain(), 0.0)
        field = self.OBJECT_BASE + self.FIELD_OFFSET
        hops = scan.find_pointers_to(snapshot, field, max_offset=0x1000)
        match = [h for h in hops if h.at == GUEST_START + self.STATIC_POINTER]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0].offset, self.FIELD_OFFSET)
        self.assertEqual(match[0].value + match[0].offset, field)

    def test_offset_beyond_limit_is_not_matched(self) -> None:
        snapshot = snapshot_from(self._region_with_chain(), 0.0)
        far = self.OBJECT_BASE + 0x4000
        hops = scan.find_pointers_to(snapshot, far, max_offset=0x100)
        self.assertNotIn(GUEST_START + self.STATIC_POINTER, [h.at for h in hops])

    def test_chain_ends_in_static_region(self) -> None:
        snapshot = snapshot_from(self._region_with_chain(), 0.0)
        field = self.OBJECT_BASE + self.FIELD_OFFSET
        chains = scan.find_pointer_chains(snapshot, field, max_offset=0x1000)
        self.assertTrue(chains)
        best = chains[0]
        self.assertEqual(best.depth, 1)
        self.assertTrue(best.root.looks_static)
        self.assertEqual(best.root.at, GUEST_START + self.STATIC_POINTER)
        self.assertEqual(best.root.offset, self.FIELD_OFFSET)

    def test_no_pointer_yields_no_chain(self) -> None:
        snapshot = snapshot_from(blank_region(), 0.0)
        # 0x11223344 als Zeiger gelesen liegt weit weg vom Ziel.
        chains = scan.find_pointer_chains(snapshot, 0x80005000, max_offset=0x10)
        self.assertEqual(chains, [])

    def test_static_classification(self) -> None:
        low = scan.PointerHop(at=0x80100000, value=0, offset=0)
        high = scan.PointerHop(at=0x81000000, value=0, offset=0)
        self.assertTrue(low.looks_static)
        self.assertFalse(high.looks_static)
