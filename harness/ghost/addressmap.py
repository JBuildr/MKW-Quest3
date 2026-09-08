"""Adresskarte: welche Gastadressen das Harness abtastet.

Warum das eine eigene, vom Nutzer zu fuellende Datei ist
--------------------------------------------------------
Die Positions- und Zeitfelder von Mario Kart Wii liegen an festen Adressen im
Wii-Speicher, aber diese Adressen stehen **nirgends im WiiCompiled-Quellcode**:
der uebersetzte Code hat keine Symbole, und die Runtime kennt das Spiel nicht
(spielspezifisches steckt nur
im YAML-Manifest, und das enthaelt Speicherbasen, keine Objektadressen).

Sie hier aus dem Gedaechtnis einzutragen waere geraten. Stattdessen laedt das
Harness sie aus einer Datei, die als **unverifiziert** gilt, bis jemand sie
geprueft und ausdruecklich freigegeben hat. Ohne Freigabe verweigert das
Harness das Aufzeichnen einer Referenz - lieber kein Ergebnis als ein falsches.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

SCHEMA_VERSION = 1


class AddressMapError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class Field:
    """Ein abzutastendes Feld im Gastspeicher."""

    name: str
    address: int | None
    kind: str  # "u8" | "u16" | "u32" | "s32" | "f32" | "vec3"
    note: str = ""
    via_pointer: int | None = None  # Gastadresse eines Zeigers, plus offset
    offset: int = 0
    # Zeitachsen-Felder (der Framezaehler) gehoeren NICHT in die Pruefsumme:
    # sie sind der Schluessel, unter dem verglichen wird, kein Messwert. Waeren
    # sie drin, unterschiede sich jede Stichprobe schon deshalb, weil die Zeit
    # weiterlaeuft.
    in_checksum: bool = True

    KINDS = ("u8", "u16", "u32", "s32", "f32", "vec3")

    def __post_init__(self) -> None:
        if self.kind not in self.KINDS:
            raise AddressMapError(
                f"Feld '{self.name}': unbekannter Typ '{self.kind}', "
                f"erlaubt: {', '.join(self.KINDS)}"
            )


@dataclasses.dataclass(frozen=True)
class AddressMap:
    """Alle Felder eines Spielstands, plus die Freigabe."""

    game_id: str
    verified: bool
    verified_by: str
    verified_note: str
    fields: tuple[Field, ...]
    source: pathlib.Path | None = None

    def require_verified(self) -> None:
        if not self.verified:
            raise AddressMapError(
                f"Die Adresskarte '{self.source or self.game_id}' ist nicht als "
                "verifiziert markiert.\n"
                "Das Harness zeichnet damit keine Referenz auf, weil eine "
                "Referenz aus ungeprueften Adressen als Abnahmekriterium "
                "wertlos ist und spaetere Phasen daran haengen.\n"
                "Trage die Adressen ein, pruefe sie an einem laufenden Spiel "
                "(siehe README, Abschnitt 'Adresskarte fuellen') und setze dann "
                '"verified": true.'
            )
        if not self.fields:
            raise AddressMapError(
                f"Die Adresskarte '{self.source or self.game_id}' ist als "
                "verifiziert markiert, enthaelt aber kein einziges Feld."
            )
        # Ein Feld hinter einem Zeiger braucht keine eigene Adresse - der Weg
        # dorthin steht in via_pointer/offset.
        blank = [
            f.name
            for f in self.fields
            if f.address is None and f.via_pointer is None
        ]
        if blank:
            raise AddressMapError(
                f"Die Adresskarte '{self.source or self.game_id}' ist als "
                "verifiziert markiert, aber diese Felder haben keine Adresse: "
                + ", ".join(blank)
            )

    def field(self, name: str) -> Field | None:
        for entry in self.fields:
            if entry.name == name:
                return entry
        return None


def _parse_address(raw: Any, context: str, *, allow_empty: bool) -> int | None:
    """Wandelt einen Adresseintrag um.

    Eine leere Adresse ist in einer noch **unverifizierten** Karte zulaessig -
    genau so sieht die mitgelieferte Vorlage aus, und sie muss ladbar bleiben,
    damit `require_verified` die verstaendliche Meldung geben kann statt eines
    Parserfehlers. In einer als verifiziert markierten Karte ist eine leere
    Adresse dagegen ein Widerspruch und wird abgelehnt.
    """
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            if allow_empty:
                return None
            raise AddressMapError(
                f"{context}: Adresse ist leer, die Karte ist aber als verifiziert "
                "markiert. Entweder die Adresse eintragen oder verified auf false "
                "setzen."
            )
        try:
            return int(text, 16 if text.lower().startswith("0x") else 10)
        except ValueError as error:
            raise AddressMapError(
                f"{context}: '{raw}' ist keine gueltige Adresse"
            ) from error
    raise AddressMapError(f"{context}: Adresse hat unerwarteten Typ {type(raw).__name__}")


def load(path: pathlib.Path | str) -> AddressMap:
    path = pathlib.Path(path)
    if not path.exists():
        raise AddressMapError(f"Adresskarte nicht gefunden: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    version = int(raw.get("schema_version", 0))
    if version != SCHEMA_VERSION:
        raise AddressMapError(
            f"{path}: Schema-Version {version}, erwartet {SCHEMA_VERSION}"
        )

    verified = bool(raw.get("verified", False))

    fields: list[Field] = []
    for index, entry in enumerate(raw.get("fields", [])):
        name = str(entry.get("name", "")).strip()
        if not name:
            raise AddressMapError(f"{path}: Feld #{index} hat keinen Namen")
        context = f"{path}: Feld '{name}'"
        pointer_raw = entry.get("via_pointer")
        # Ein Feld hinter einem Zeiger darf keine eigene Adresse haben, auch in
        # einer verifizierten Karte nicht - der Weg steht in via_pointer/offset.
        has_pointer = pointer_raw not in (None, "")
        fields.append(
            Field(
                name=name,
                address=_parse_address(
                    entry.get("address", ""), context,
                    allow_empty=(not verified) or has_pointer,
                ),
                kind=str(entry.get("kind", "")).strip(),
                note=str(entry.get("note", "")),
                via_pointer=(
                    None
                    if pointer_raw in (None, "")
                    else _parse_address(pointer_raw, context + " (via_pointer)", allow_empty=not verified)
                ),
                offset=int(entry.get("offset", 0)),
                in_checksum=bool(entry.get("in_checksum", True)),
            )
        )

    return AddressMap(
        game_id=str(raw.get("game_id", "")),
        verified=verified,
        verified_by=str(raw.get("verified_by", "")),
        verified_note=str(raw.get("verified_note", "")),
        fields=tuple(fields),
        source=path,
    )


def sample_named(memory: Any, address_map: AddressMap) -> list[tuple[str, float | int]]:
    """Wie `sample`, aber jeder Wert mit seinem Namen.

    Ein vec3 liefert drei Werte; ohne diese Zuordnung laesst sich eine Ausgabe
    nicht mehr richtig beschriften.
    """
    named: list[tuple[str, float | int]] = []
    for entry in address_map.fields:
        base = entry.address
        if entry.via_pointer is not None:
            base = memory.follow_pointer(entry.via_pointer) + entry.offset
        if entry.kind == "vec3":
            for axis, value in zip("xyz", memory.read_vec3(base)):
                named.append((f"{entry.name}.{axis}", value))
        elif entry.kind == "u8":
            named.append((entry.name, memory.read_u8(base)))
        elif entry.kind == "u16":
            named.append((entry.name, memory.read_u16(base)))
        elif entry.kind == "u32":
            named.append((entry.name, memory.read_u32(base)))
        elif entry.kind == "s32":
            named.append((entry.name, memory.read_s32(base)))
        elif entry.kind == "f32":
            named.append((entry.name, memory.read_f32(base)))
    return named


def checksum_fields(address_map: AddressMap) -> AddressMap:
    """Die Karte ohne die Zeitachsen-Felder."""
    return dataclasses.replace(
        address_map, fields=tuple(f for f in address_map.fields if f.in_checksum)
    )


def sample(memory: Any, address_map: AddressMap) -> list[float | int]:
    """Liest alle Felder der Karte und gibt die Werte in Kartenreihenfolge zurueck.

    `memory` ist ein `guest_memory.GuestMemory`. Die Reihenfolge ist die der
    Datei und geht in die Pruefsumme ein - eine umsortierte Karte erzeugt
    also andere Pruefsummen, was gewollt ist: sie beschreibt einen anderen Test.
    """
    values: list[float | int] = []
    for entry in address_map.fields:
        base = entry.address
        if entry.via_pointer is not None:
            base = memory.follow_pointer(entry.via_pointer) + entry.offset
        if entry.kind == "u8":
            values.append(memory.read_u8(base))
        elif entry.kind == "u16":
            values.append(memory.read_u16(base))
        elif entry.kind == "u32":
            values.append(memory.read_u32(base))
        elif entry.kind == "s32":
            values.append(memory.read_s32(base))
        elif entry.kind == "f32":
            values.append(memory.read_f32(base))
        elif entry.kind == "vec3":
            values.extend(memory.read_vec3(base))
    return values
