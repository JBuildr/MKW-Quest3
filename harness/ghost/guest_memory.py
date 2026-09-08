"""Lesender Zugriff auf den Wii-Adressraum eines laufenden WiiCompiled-Prozesses.

Warum das geht ohne Aenderung am Spiel
--------------------------------------
WiiCompiled bildet den kompletten 4-GiB-Gastadressraum flach an eine **feste**
Hostadresse ab, damit ein Gastzugriff zu `*(T*)(base + addr)` kompiliert
(`runtime/include/guest_flat_memory.h:4-16`). Die Basis ist pro Architektur
eine Konstante, kein ASLR-Wert. Damit ist

    Hostadresse = Basis + Gastadresse

und ein externer Beobachter kann jede bekannte Gastadresse lesen, ohne dass am
uebersetzten Code oder an der Runtime irgendetwas geaendert werden muss.

Genau diese Eigenschaft macht das Harness architekturuebergreifend brauchbar:
auf aarch64 aendert sich nur die Konstante, nicht das Verfahren.

Die Basiswerte stammen aus `runtime/include/guest_flat_memory.h:18-42`.
"""

from __future__ import annotations

import ctypes
import dataclasses
import pathlib
import platform
import struct
import sys

GUEST_SPACE_SIZE = 0x1_0000_0000

# runtime/include/guest_flat_memory.h:19-38 - je Architektur eine feste Basis.
FLAT_GUEST_BASE = {
    "x86_64": 0x0000_1000_0000_0000,
    "aarch64-apple": 0x0000_0080_0000_0000,
    "aarch64": 0x0000_0010_0000_0000,
}

# Wii-Speicherkarte, runtime/include/memory.h:14-28.
MEM1_CACHED_BASE = 0x8000_0000
MEM1_SIZE = 24 * 1024 * 1024
MEM2_CACHED_BASE = 0x9000_0000
MEM2_SIZE = 128 * 1024 * 1024


class GuestMemoryError(RuntimeError):
    pass


def flat_base_for(target: str | None = None) -> int:
    """Liefert die flache Gastbasis fuer eine Zielarchitektur.

    Ohne Argument wird die Architektur des laufenden Rechners benutzt. Fuer
    Phase 1 und 2 kann das Ziel ausdruecklich angegeben werden, damit ein
    x86-Host die Basis eines aarch64-Laufs ausrechnen kann.
    """
    if target is None:
        machine = platform.machine().lower()
        if machine in ("amd64", "x86_64"):
            target = "x86_64"
        elif machine in ("arm64", "aarch64"):
            target = "aarch64-apple" if sys.platform == "darwin" else "aarch64"
        else:
            raise GuestMemoryError(
                f"Keine flache Gastbasis fuer Architektur '{machine}' bekannt. "
                "guest_flat_memory.h kennt nur x86_64 und aarch64."
            )
    if target not in FLAT_GUEST_BASE:
        raise GuestMemoryError(
            f"Unbekanntes Ziel '{target}', bekannt: {sorted(FLAT_GUEST_BASE)}"
        )
    return FLAT_GUEST_BASE[target]


def host_address(guest_address: int, target: str | None = None) -> int:
    """Rechnet eine Gastadresse in die Hostadresse des Gast-Views um."""
    if not 0 <= guest_address < GUEST_SPACE_SIZE:
        raise GuestMemoryError(
            f"Gastadresse ausserhalb des 4-GiB-Raums: 0x{guest_address:X}"
        )
    return flat_base_for(target) + guest_address


def is_mapped_ram(guest_address: int, length: int = 1) -> bool:
    """Grobe Vorabpruefung: liegt der Bereich in MEM1 oder MEM2 (cached)?

    Nur eine Bequemlichkeit fuer verstaendliche Fehlermeldungen. Die
    tatsaechliche Abbildung entscheidet der Prozess, nicht diese Funktion.
    """
    end = guest_address + length
    in_mem1 = (
        MEM1_CACHED_BASE <= guest_address and end <= MEM1_CACHED_BASE + MEM1_SIZE
    )
    in_mem2 = (
        MEM2_CACHED_BASE <= guest_address and end <= MEM2_CACHED_BASE + MEM2_SIZE
    )
    return in_mem1 or in_mem2


# --------------------------------------------------------------------------
# Prozessanbindung
# --------------------------------------------------------------------------


class _Reader:
    """Gemeinsame Schnittstelle der plattformabhaengigen Leser."""

    def read(self, host_addr: int, size: int) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        pass


class _WindowsReader(_Reader):
    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400

    def __init__(self, pid: int) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        handle = kernel32.OpenProcess(
            self.PROCESS_VM_READ | self.PROCESS_QUERY_INFORMATION, False, pid
        )
        if not handle:
            raise GuestMemoryError(
                f"OpenProcess({pid}) fehlgeschlagen "
                f"(GetLastError={ctypes.get_last_error()}). "
                "Laeuft der Prozess, und reichen die Rechte?"
            )
        self._handle = handle

    def read(self, host_addr: int, size: int) -> bytes:
        buffer = (ctypes.c_ubyte * size)()
        read_bytes = ctypes.c_size_t(0)
        ok = self._kernel32.ReadProcessMemory(
            self._handle,
            ctypes.c_void_p(host_addr),
            ctypes.byref(buffer),
            ctypes.c_size_t(size),
            ctypes.byref(read_bytes),
        )
        if not ok or read_bytes.value != size:
            raise GuestMemoryError(
                f"ReadProcessMemory an 0x{host_addr:X} ({size} Byte) fehlgeschlagen "
                f"(GetLastError={ctypes.get_last_error()}, "
                f"gelesen={read_bytes.value}). "
                "Die Seite kann geschuetzt oder nicht abgebildet sein - "
                "guest_flat_memory schuetzt MMIO- und EFB-Seiten absichtlich."
            )
        return bytes(buffer)

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class _LinuxReader(_Reader):
    def __init__(self, pid: int) -> None:
        path = pathlib.Path(f"/proc/{pid}/mem")
        try:
            self._handle = path.open("rb", buffering=0)
        except OSError as error:
            raise GuestMemoryError(
                f"{path} nicht lesbar: {error}. "
                "Unter Linux braucht das ptrace-Rechte (CAP_SYS_PTRACE oder "
                "kernel.yama.ptrace_scope=0)."
            ) from error

    def read(self, host_addr: int, size: int) -> bytes:
        try:
            self._handle.seek(host_addr)
            data = self._handle.read(size)
        except OSError as error:
            raise GuestMemoryError(
                f"Lesen an 0x{host_addr:X} ({size} Byte) fehlgeschlagen: {error}"
            ) from error
        if data is None or len(data) != size:
            raise GuestMemoryError(
                f"Kurzer Lesevorgang an 0x{host_addr:X}: "
                f"{0 if data is None else len(data)} statt {size} Byte"
            )
        return data

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._handle.close()
            self._handle = None


@dataclasses.dataclass
class GuestMemory:
    """Lesender Zugriff auf den Gastadressraum eines Prozesses.

    Big-Endian, weil der Gastspeicher die Byte-Reihenfolge der Wii behaelt -
    die Runtime tauscht erst beim Zugriff durch den uebersetzten Code
    (`runtime/include/memory_access.h:249-259`).
    """

    pid: int
    target: str | None = None

    def __post_init__(self) -> None:
        self._base = flat_base_for(self.target)
        if sys.platform == "win32":
            self._reader: _Reader = _WindowsReader(self.pid)
        elif sys.platform.startswith("linux"):
            self._reader = _LinuxReader(self.pid)
        else:
            raise GuestMemoryError(
                f"Kein Speicherleser fuer Plattform '{sys.platform}' implementiert"
            )

    @property
    def flat_base(self) -> int:
        return self._base

    def read_bytes(self, guest_address: int, size: int) -> bytes:
        if not 0 <= guest_address < GUEST_SPACE_SIZE:
            raise GuestMemoryError(
                f"Gastadresse ausserhalb des 4-GiB-Raums: 0x{guest_address:X}"
            )
        return self._reader.read(self._base + guest_address, size)

    def read_u32(self, guest_address: int) -> int:
        return struct.unpack(">I", self.read_bytes(guest_address, 4))[0]

    def read_s32(self, guest_address: int) -> int:
        return struct.unpack(">i", self.read_bytes(guest_address, 4))[0]

    def read_u16(self, guest_address: int) -> int:
        return struct.unpack(">H", self.read_bytes(guest_address, 2))[0]

    def read_u8(self, guest_address: int) -> int:
        return self.read_bytes(guest_address, 1)[0]

    def read_f32(self, guest_address: int) -> float:
        return struct.unpack(">f", self.read_bytes(guest_address, 4))[0]

    def read_vec3(self, guest_address: int) -> tuple[float, float, float]:
        return struct.unpack(">fff", self.read_bytes(guest_address, 12))

    def follow_pointer(self, guest_address: int) -> int:
        """Liest einen Gastzeiger und gibt die Gastadresse zurueck, auf die er zeigt."""
        return self.read_u32(guest_address)

    def close(self) -> None:
        self._reader.close()

    def __enter__(self) -> "GuestMemory":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
