# Ghost harness

Acceptance criterion for all phases from 1 onward: play back a ghost file, then
compare the finish time and position checksums against a reference. If anything
deviates, the port is not finished, no matter how good it looks otherwise.

> **No game data.** No command of this tool ever copies a ghost file, an asset
> or memory contents. Paths come from the caller and are only ever read.
> Reference files contain only numbers from your own run: times and one-way
> checksums from which nothing can be reconstructed.

## Why it is built this way

WiiCompiled has **no ghost or replay infrastructure whatsoever**: a search across
`runtime/` and `translator/` finds not a single hit for `.rkg` or ghost
playback. Ghosts are handled by the translated game code itself. On top of that,
the runtime accepts **no command-line options**
(`runtime/src/main.cpp:1327`), and `Config.toml` has no debug or telemetry
section.

So there was nothing to hook into. The approach instead uses a property of the
memory mapping:

> WiiCompiled maps the 4 GiB guest address space flat to a **fixed** host
> address, so that a guest access compiles to `*(T*)(base + addr)`
> (`runtime/include/guest_flat_memory.h:4-16`). The base is a constant, not an
> ASLR value.

This gives `host address = base + guest address`, and an external observer can
read any known guest address, **without changing a single line of WiiCompiled**.
That is the decisive point for the port: on aarch64 only the constant changes,
not the method.

| Target | Base | Source |
|---|---|---|
| `x86_64` | `0x0000_1000_0000_0000` | `guest_flat_memory.h:19-21` |
| `aarch64-apple` | `0x0000_0080_0000_0000` | `guest_flat_memory.h:23-27` |
| `aarch64` | `0x0000_0010_0000_0000` | `guest_flat_memory.h:29-38` |

## Structure

| File | Purpose |
|---|---|
| `rkg.py` | Reads the header of a ghost file (track, finish time, lap times) |
| `guest_memory.py` | Address calculation and read access to a running process |
| `addressmap.py` | Loads the address map, refuses unverified maps |
| `reference.py` | Save, load and exactly compare references |
| `ghostrun.py` | Command line |
| `addresses/` | Address maps per game version |
| `tests/` | 72 tests, run without game data and without a running game |

## Commands

```
python harness/ghost/ghostrun.py inspect <ghost.rkg>
python harness/ghost/ghostrun.py verify-layout <directory> [...]
python harness/ghost/ghostrun.py probe  --pid <pid>
python harness/ghost/ghostrun.py sample --pid <pid>
python harness/ghost/ghostrun.py scan   --pid <pid> [--out map.json]
python harness/ghost/ghostrun.py record --pid <pid> --ghost <g.rkg> --out ref.json
python harness/ghost/ghostrun.py check  --pid <pid> --ghost <g.rkg> --reference ref.json
python harness/ghost/ghostrun.py compare ref.json run.json
```

Exit codes: `0` OK · `1` deviation · `2` invocation problem · `3` blocked
(address map unverified, memory not readable).

`compare` needs **no** running game. This makes it possible to check a run
recorded on an aarch64 device against the x86 reference without both machines
having to be available at the same time, which is the intended path for
phase 1.

## Tests

```
python -m unittest discover -s harness/ghost/tests -t harness/ghost/tests
```

**72 tests, all green.** They need no game file: the ghost headers are
generated synthetically. Among other things, the tests cover that the bit
fields do not overwrite each other, that the address calculation matches the
constants from `guest_flat_memory.h`, that a deviation in the last float bit
changes the checksum, that the comparison has **no** tolerance, and that a
saved reference contains no ghost payload data.

## Verification of the ghost parser

The field layout of the header is not assumed but empirically established.
`verify-layout` over the existing ghost collection produced:

```
Geprueft:         746
Layout stimmig:   742  (99.5 %)
Nicht lesbar:     0
Strecken-IDs:     32 verschiedene, 0..31
```

Three independent pieces of evidence that the bit fields are positioned correctly:

1. **0 files without RKGD magic** out of 746.
2. **The track IDs cover exactly 0–31**, the 32 tracks of Mario Kart Wii, no
   value above or below. This confirms the 6-bit field at its position.
3. **The lap times add up to the finish time to the millisecond.**
   Example from `inspect`: 40387 + 37665 + 41350 = 119402 ms = 1:59.402. If a
   bit field were shifted, this could not work out across hundreds of files.

**Not determined:** Four files (two unique ones, each present twice, all from a
Retro Rewind folder) report a lap count of 7 with a plausible finish time.
Whether Retro Rewind uses the field differently or whether something else is
stored there was not clarified. The harness reads them but marks them as
questionable.

Also **not determined**: the meaning of the header region `0x20`–`0x87`
(presumably Mii data, origin, checksums). It is deliberately not interpreted
because the harness does not need it.

## Address map: filled in and confirmed

`addresses/rmcp01-pal.json` is **filled in and marked as verified**. The
addresses were not copied from elsewhere but measured on a running game
(RetroRewind, 2026-09-05):

| Field | Access | Evidence |
|---|---|---|
| `race_frame` | `0x8034750C`, static | measured repeatedly at 60.0–60.4 steps/s |
| `kart_point_0..3` | `[0x80398030…3C]` + `0x58`, `vec3` | see counter-test below |

**The counter-test that decided it:** While the frame counter kept running at
exactly 60/s, all four points **froze as soon as the kart stood still** and
started moving again when it pulled away. So they are neither a ghost nor a
track animation, but the player's kart.

They were found through the physics: an adjacent vector pointed exactly along
the direction of movement (cos = 1.00000 across all samples).

**Why the detour via pointers was mandatory:** The object addresses move
between races; observed was `0x809C9B70` → `0x809CAD90`. A bare heap address
would be worthless after the first restart. The static pointers
`0x80398030…3C` stay put.

**Not determined:** what exactly the four points represent. They form two
pairs (points 0/1 and 2/3 each share x and y), lie 20–70 units apart and are
probably wheels or corners of the kart. For the parity check this is
irrelevant: they sit behind the complete physics and show every deviation.

Also open: `race_timer_ms` (no monotonic counter with ~1000/s found; MKW
probably stores the race time differently) and a static path to the speed.

### A peculiarity of the checksum

The frame counter is **deliberately not** part of the checksum (`in_checksum:
false`). It is the time axis along which the comparison is made; if it were
included, every sample would already differ simply because time keeps running.

And: the kart jitters in the sixth decimal place while standing still
(suspension). Two measurements at different points in time are therefore
legitimately different. Comparison is done **per frame**, never by wall clock.

## When the map has to be determined again

If the addresses no longer match after an update of WiiCompiled or Retro
Rewind, the harness refuses to work as soon as `verified` is set to `false`.

The reason: the position and time fields of Mario Kart Wii lie at fixed
addresses in Wii memory, but **these addresses appear nowhere in the
WiiCompiled source code**. The translated code has no symbols, and the YAML
manifest contains only memory bases, no object addresses (see
the WiiCompiled sources). Entering them here without having
checked them would be guesswork.

This is how they are determined:

1. Start the game, find the PID, run `probe --pid <pid>`. This confirms that
   guest memory is readable at all, and separates a permissions problem from an
   address problem.
2. **Run `scan --pid <pid>`** (see below). This finds the addresses
   empirically. Alternatively, enter them by hand if they are known.
3. Cross-check with `sample --pid <pid>`: if the kart is standing still,
   `player_position` must not change; if it is driving, it must change
   steadily; the race time must match the display in the game.
4. Only then set `"verified": true` and fill in `verified_by`.

## The address scanner

```
python harness/ghost/ghostrun.py scan --pid <pid> --out harness/ghost/addresses/proposal.json
```

The scanner needs **no external address source**. The fields being looked for
give themselves away through their behavior, and it measures that in two phases:

| Phase | What you do | What is looked for |
|---|---|---|
| 1 (standstill) | leave the kart standing, **in the race** | values that stay constant |
| 2 (driving) | drive off, as straight as possible | which of those change steadily |

Before each phase a countdown runs so that both hands can stay on the
controller. The procedure takes around 20 seconds.

**The characteristics it separates by:**

- **Position**: constant at standstill, while driving with a path that leads
  predominantly in *one* direction (straightness = straight-line distance ÷
  path length). A randomly jittering value keeps reversing and hardly gets
  anywhere; that is exactly what sorts it out.
- **Speed**: near zero at standstill, clearly different from that while
  driving. It explicitly does *not* have to change while doing so: at constant
  driving it rightly stays the same.
- **Counters**: strictly monotonically increasing at ~60 steps/s (frames) or
  ~1000/s (milliseconds). The step size per second is itself the
  confirmation.
- **Pairs**: position and speed usually lie only a few hundred bytes apart in
  the same game object. A pair is a much stronger indication than two
  individual finds and is therefore shown first.

**A peculiarity you need to know about:** The scanner samples in 4-byte steps
and therefore also finds *shifted neighboring triples*, for example four bytes
before the real position, where a real component meets neighboring data. The
ranking therefore prefers triples in which **all three axes** move: a real
position vector does that, a shifted triple usually catches only one moving
component.

Protected pages (MMIO, EFB, executable guard) are normal and are skipped
block by block instead of being treated as errors; the output states how much
of the region was readable.

If the scanner finds nothing, these are the usual causes, and it announces them
itself: one phase took place in the menu instead of in the race, the kart did
roll after all in phase 1, or the data lies in MEM2 (then `--region mem2`).

**The scanner never sets `verified` to `true`.** With `--out` it writes a
proposal with `"verified": false` and a note that it is unchecked. The
cross-check with `sample` and the sign-off remain a human decision: a wrong
address would produce a wrong reference, and all subsequent phases depend on
that.

What makes sense is the **minimum** that makes a physics deviation visible. The
more fields end up in the checksum, the more noise it captures (camera,
particles, RNG state) that has nothing to do with physics parity and turns the
test falsely red.

## Determinism: measured, not yet concluded

Two playbacks **of the same ghost** on the same machine were compared against
each other. This is the test that decides whether the whole harness is fit for
purpose: whatever is not reproducible here is no use for the aarch64 comparison
either.

| Run | Result |
|---|---|
| without anchor, ~128 s | one dominant shift of −505 frames, 5095 exact matches there; in the range 8–104 s 96–97 % identical |
| with anchor, 60 s | shift only **−6 frames**, **87.3 %** identical there (3028 of 3470) |

**What this proves:** Playback is deterministic. In each case there is exactly
*one* dominant shift with thousands of bit-identical checksums; chance looks
different. The method really does measure the physics.

**What is open:** A bit-identical run over the full length has not yet been
achieved. Two causes have been identified and fixed, but not re-measured:

1. **The start time was arbitrary.** Fixed by `--wait-for-motion`: the zero
   point is the first frame with real movement, not the moment of the
   command.
2. **The anchor was not frame-accurate.** The first version compared two
   arbitrary queries; depending on how they fell on frames, the start frame
   was off, by exactly the measured 6 frames. Fixed: only positions from two
   directly consecutive frames are compared.

**Not determined:** whether a residue remains after these two corrections. If
so, `--per-field` tells which of the four points wobbles; the checksums are
then additionally stored per field.

Also open and observed: at the end of a run (last ~12 s) the runs diverged
completely. Presumably they covered different sections of the post-race
sequence; this is not proven. A fixed length via `--frames` sidesteps the
problem instead of clarifying it.

## What is still missing

| Item | Status |
|---|---|
| Ghost parser | done, confirmed on 746 files |
| Address calculation x86 and aarch64 | done, tested against the runtime header |
| Save, load, compare reference | done, tested |
| Address scanner | done, tested against synthetic memory |
| Pointer search | done, used on the running game |
| Address map RMCP01 | **filled in and confirmed on the game** |
| Reading from a running process | **tried out on the running game** |
| `record` / `compare` | exercised end to end: green on equality, red on a changed checksum |
| Anchor on first movement | built in, **not re-measured after the frame correction** |
| Bit-identical run over full length | **not yet achieved**, see above |
| `race_timer_ms`, speed | open, see above |
| Automatic starting of the game and selecting the ghost | **not built** |

The last item is deliberately open. Without a command line and without a script
mode, the only option would be to operate the game menus with simulated inputs,
which is fragile and hard to maintain. The cleaner way would be a minimally
invasive addition to WiiCompiled (a switch in `Config.toml` that plays back a
ghost and writes the telemetry), which belongs in `patches/` as a patch series.
That can only be built once a product build exists, and that needs the user's
disc image.

Until then, the run is started by hand and the harness attached with `--pid`.
