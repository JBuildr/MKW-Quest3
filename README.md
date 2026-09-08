<h1 align="center">MKW QUEST</h1>
<p align="center"><b>Mario Kart Wii on the Meta Quest 3, built from your own disc.</b></p>
<p align="center">
  Native arm64 build through WiiCompiled's static recompilation, rendered by aurora on Vulkan.<br>
  Retro Rewind supported. No game data in this repository, ever.
</p>

<pre align="center">
   ______________________________________________________________
  |  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  |
  |    ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##    |
  |  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  |
  |______________________________________________________________|
                    3  .  .  2  .  .  1  .  .  GO
</pre>

---

## Trophy Room

**First place, always: the WiiCompiled team.**
This project exists only because of the people behind
[WiiCompiled](https://github.com/patchzyy/Wiicompiled), the static
recompilation of Mario Kart Wii for PC: the translator, the runtime, the
embedded [aurora](https://github.com/encounter/aurora) GX layer, the Retro
Rewind integration and the installer that turns a player's own disc into a
native build. Everything here is a thin Android and Quest layer on top of
their work.

Second place: [encounter](https://github.com/encounter) for aurora, nod and
the prebuilt Dawn that the Quest build links against.

---

## Race Status

| Lap | State |
|---|---|
| Boots, menus, sound, Xbox controller; menus at 60 fps | Done |
| 3D models (characters, karts, wheels, trophies, Miis) | Done since 2026-09-07 |
| Races on the track | Done; 18 to 30 fps with 12 karts, 45 in time trials. The CPU expansion of the workaround costs about half (open item) |
| Online play (Retro WFC) | Done since 2026-09-08, see Pit stop below |
| Video panels in the main-menu buttons | Done since 2026-09-08 (same driver fault, direct vertex formats with an odd stride) |

The Quest 3's Adreno 740 shader compiler miscomputes the storage-buffer word
address `(base + index * stride + offset) >> 2` when the terms are not
multiples of 4 (it distributes the shift over the sum), so every indexed
vertex fetch and every direct format with an odd stride read one word too
early. The build works around it by expanding such draws on the CPU into a
4-byte-aligned direct vertex layout, where every address term is a multiple
of 4. The workaround is on by default for Android and can be switched off in
`Config.toml` for comparison (`[debug] aurora_deindex = false`). It costs CPU
time in races (see Race Status); a GPU-side formulation that avoids the
miscompile is the next thing to try. The compute-shader reproduction that
pins the fault ships with the build (`[debug] aurora_gx_debug = 2048`).

---

## Rules of the Track

This repository contains **no game data and never will**: no disc image, no
`main.dol`, no `StaticR.rel`, no asset, no ghost, no translated game code.
Nothing here helps you obtain any of that.

You bring:

- **Your own Mario Kart Wii PAL disc (`RMCP01`)**, dumped from your own copy,
  as ISO, WBFS, RVZ, WIA, GCZ, CISO or NKit. The dump is checked against the
  game ID and the hashes of `main.dol` and `StaticR.rel` that WiiCompiled pins;
  a different game, region or disc revision is refused.
- Optional: a **Retro Rewind 6** folder containing `Binaries\Code.pul`. The
  builder's **Get / update Retro Rewind** button (`tools\Get-RetroRewind.ps1`)
  downloads it from the Retro Rewind update server and keeps it current; a
  folder that WheelWizard manages works just as well.

---

## Starting Grid: what your PC needs

- Windows 10/11 x64, **16 GB RAM** (the final link needs close to 15 GB; the
  scripts limit parallelism for that reason), about 40 GB free disk.
- Android SDK with **NDK 28.x**, **build-tools 35.0.0**, **platform
  android-34**, the **CMake** package (brings CMake and Ninja) and
  `platform-tools` (adb). Android Studio's SDK Manager installs all of them.
- A JDK 17 or newer (`javac`), Git for Windows, .NET SDK 8 or newer.
- A Quest 3 in developer mode, connected over USB with USB debugging allowed.

---

## Grand Prix: building the APK

Get the tools either from the **Releases** page (`MKW-Quest3-Port-<version>.zip`:
builder, scripts, patches and Android packaging files, nothing else) or by
cloning this repository; both work the same way. Unpack or clone anywhere
**except** inside the Android SDK or a path with spaces, then double-click
**`Quest-Builder.cmd`** in that folder. Pick your disc
image, optionally the Retro Rewind folder, check that every requirement shows
OK, then:

- **Build APK** clones WiiCompiled at the pinned commit into the work
  directory, applies the patches, builds the translator, validates and
  extracts your disc, translates the game (the long step, plus the native
  build: an hour or more the first time), and writes `android\out\mkw-quest.apk`
  inside the work directory (the APK carries the translated game code, so it
  never lands in this repository).
  Every step is skipped when its result already exists, so a failed run
  continues where it stopped.
- **Install on Quest** installs the APK over adb, grants the storage
  permission and, with the checkbox ticked, pushes the game data once.

The same pipeline runs from a terminal:

```powershell
.\tools\Invoke-QuestPipeline.ps1 -Workspace D:\mkw-quest-work -DiscImage D:\dumps\mkw.wbfs `
    -RetroRewindDirectory "$env:APPDATA\CT-MKWII\RetroRewind\RetroRewind6" -Install -PushAssets
```

Start the app from the headset's app library (unknown sources). Starting it
with `adb shell am start` is not useful: Horizon OS pauses the panel at once.
Logs: `adb logcat -s mkw`.

### Pit lane: what happens under the hood

| Step | Script | What it does |
|---|---|---|
| 1 | `tools\New-StandaloneWorkspace.ps1` | `git clone` of WiiCompiled at `e6f9b21`, Fix AA, patches `0001` to `0003`, `dotnet build` of the translator |
| 2 | `tools\Import-DiscImage.ps1` | `nodtool info` (game ID), `nodtool extract` to `GameAssets\DATA`, hash check, `Assets\main.dol` + `StaticR.rel` |
| 3 | `tools\Translate-Game.ps1` | `translate-recursive`, `emit-base-manifest`, optional `translate-mod` (Retro Rewind), `generate-data-init`, `emit-build-shards`, all with `--target-os android` |
| 4 | `tools\Build-Quest.ps1` | CMake with the NDK toolchain (`arm64-v8a`, `android-34`, prebuilt Dawn for Android), Ninja, APK packaging without Gradle |
| 5 | `android\deploy.ps1` | `adb install`, `appops` storage grant, push of `DATA` and Retro Rewind to `/sdcard/MKW`, `Config.toml` |

`nodtool` comes from [encounter/nod](https://github.com/encounter/nod)
(MIT/Apache-2.0) and is downloaded once if the WiiCompiled toolkit is not
installed. The Retro WFC online payload is fetched by the translator from the
Retro WFC server during the build, exactly as WiiCompiled does on PC; a build
without it (`Translate-Game.ps1 -SkipRetroWfc`) plays offline only.

A legacy mode reuses the translation of an installed WiiCompiled PC build
instead of a disc image (`tools\New-QuestWorkspace.ps1`,
`tools\Regenerate-AndroidTranslation.ps1`); the GUI falls back to it when no
disc image is given. It is part of the source tree only, not of the release
archive.

### Pit stop: updating Retro Rewind

Retro Rewind updates often, and the Retro WFC server only lets the current
pack version online (error 22010 otherwise). The mod code is compiled into the
app, so every update means a rebuild:

1. Click **Get / update Retro Rewind** in the builder (or let WheelWizard
   update the folder, if that is where it came from). The updater reads
   `version.txt`, fetches only the delta packages since that version and
   applies them in order; the builder shows whether the folder is current.
2. Run **Build APK** and **Install on Quest** (with the game-data checkbox)
   again. The pipeline notices the new `Code.pul`, translates only the mod
   again, rebuilds only the mod's part of the native code (roughly 15 to 30
   minutes) and pushes only the files that changed on the headset.

Offline modes and time trials keep working with an old pack; only online play
needs the rebuild.

### Tuning: switches without rebuilding

`/sdcard/MKW/WiiCompiled/Config.toml` accepts a `[debug]` section:

```toml
[debug]
aurora_deindex = true      # the Adreno workaround; false shows the driver fault
aurora_gx_debug = 0        # renderer diagnostics bit mask (2048 = compute-shader reproduction of the driver fault)
dawn_validation = false
```

---

## Garage: repository layout

| Path | Contents |
|---|---|
| `patches/` | the three patches against WiiCompiled `e6f9b21`: Android target, translator `--target-os`, the Quest renderer work (Adreno workaround, JNI guard, debug switches) |
| `android/` | manifest, Java activity, packaging and deploy scripts |
| `tools/` | pipeline scripts and the GUI |
| `harness/ghost/` | ghost/replay parity harness (PC and Quest) |

Real paths to your installation go into `local.paths.md`, which is
git-ignored.

---

## License

This project is licensed under the **GNU General Public License v3.0**, see
[`LICENSE`](LICENSE). WiiCompiled is GPL v3 and its README requires every
Mario Kart Wii distribution that uses it to be GPL v3 as well; this
repository and every build made from it follow that.

The port is written against WiiCompiled commit `e6f9b21`; the work directory
is a clone at exactly that commit, so newer upstream changes cannot break the
patches. aurora is MIT, nodtool is MIT/Apache-2.0, the remaining components
are listed in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).

Mario Kart and Wii are trademarks of Nintendo. This project is not affiliated
with, endorsed by or connected to Nintendo. No game data of any kind is part
of this project or of any build made from it.
