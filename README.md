<pre align="center">
 __  __  _  ____      __   ___   _   _  _____  ____  _____
|  \/  || |/ /\ \    / /  / _ \ | | | || ____|/ ___||_   _|
| |\/| || ' /  \ \/\/ /  | | | || | | ||  _|  \___ \  | |
| |  | || . \   \    /   | |_| || |_| || |___  ___) | | |
|_|  |_||_|\_\   \/\/     \__\_\ \___/ |_____||____/  |_|

   ______________________________________________________________
  |  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  |
  |    ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##    |
  |  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  |
  |______________________________________________________________|
</pre>

<p align="center"><b>Mario Kart Wii on the Meta Quest 3, built from your own disc.</b></p>
<p align="center">
  Native arm64 build through WiiCompiled's static recompilation, rendered by aurora on Vulkan.<br>
  Retro Rewind supported. No game data in this repository, ever.
</p>

<p align="center">
  <img alt="Platform: Meta Quest 3" src="https://img.shields.io/badge/platform-Meta%20Quest%203-1a73e8">
  <img alt="Built from your own disc" src="https://img.shields.io/badge/game%20data-bring%20your%20own%20disc-e53935">
  <img alt="Retro Rewind supported" src="https://img.shields.io/badge/Retro%20Rewind-6.x-43a047">
  <img alt="License: GPL v3" src="https://img.shields.io/badge/license-GPL%20v3-fbc02d">
</p>

<pre align="center">
                    3  .  .  2  .  .  1  .  .  GO
</pre>

<table align="center">
  <tr>
    <td align="center"><img src="images/main-menu.jpg" alt="Main menu of Retro Rewind on the Quest 3, video panels intact, 60 fps" width="420"><br><sub>Main menu, 60 fps</sub></td>
    <td align="center"><img src="images/character-select.jpg" alt="Character select with every icon and the Mii rendered correctly" width="420"><br><sub>Character select</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="images/vehicle-select.jpg" alt="Vehicle select with all kart and bike models" width="420"><br><sub>Vehicle select</sub></td>
    <td align="center"><img src="images/retro-wfc.jpg" alt="Retro WFC online lobby with 453 players online" width="420"><br><sub>Retro WFC, online</sub></td>
  </tr>
</table>
<p align="center"><sub>Screenshots from the headset; the fps counter top right is the game's own.</sub></p>

---

## Select Your Course

| Course | What is there |
|---|---|
| [Trophy Room](#trophy-room) | who made this possible |
| [Race Results](#race-results) | what runs on the headset today |
| [Rules of the Track](#rules-of-the-track) | what you bring, what this repository never contains |
| [Starting Grid](#starting-grid-what-your-pc-needs) | what your PC needs |
| [Grand Prix](#grand-prix-building-the-apk) | building the APK, lap by lap |
| [Pit Lane](#pit-lane-what-happens-under-the-hood) | what the scripts do |
| [Pit Stop](#pit-stop-updating-retro-rewind) | updating Retro Rewind |
| [Tuning](#tuning-switches-without-rebuilding) | switches without rebuilding |
| [Garage](#garage-repository-layout) | repository layout |
| [Final Standings](#final-standings-license) | license and trademarks |

---

## Trophy Room

**1st place, always: the WiiCompiled team.**
This project exists only because of the people behind
[WiiCompiled](https://github.com/patchzyy/Wiicompiled), the static
recompilation of Mario Kart Wii for PC: the translator, the runtime, the
embedded [aurora](https://github.com/encounter/aurora) GX layer, the Retro
Rewind integration and the installer that turns a player's own disc into a
native build. Everything here is a thin Android and Quest layer on top of
their work.

**2nd place: [encounter](https://github.com/encounter)** for aurora, nod and
the prebuilt Dawn that the Quest build links against.

**3rd place: the Retro Rewind team** for the pack and the Retro WFC servers
that keep online play alive.

**Pit crew:** this port was vibe-coded with Claude Fable 5.1. The renderer
investigation, the patches, the build scripts and this README were written
in that collaboration; every result was verified on the headset.

---

## Race Results

| Position | Lap | Result |
|---|---|---|
| 1st | Boot, menus, sound, Xbox controller | Finished. Menus at 60 fps |
| 1st | 3D models (characters, karts, wheels, trophies, Miis) | Finished since 2026-09-07 |
| 1st | Video panels in the main-menu buttons | Finished since 2026-09-08 (same driver fault, direct vertex formats with an odd stride) |
| 1st | Online play (Retro WFC) | Finished since 2026-09-08, see [Pit Stop](#pit-stop-updating-retro-rewind) |
| 2nd | Races on the track | Running, 18 to 30 fps with 12 karts, 45 fps in time trials. The CPU expansion of the workaround costs about half; still on the track |

> **Lakitu's note on the Adreno 740.** The Quest 3's shader compiler
> miscomputes the storage-buffer word address
> `(base + index * stride + offset) >> 2` when the terms are not multiples of
> 4 (it distributes the shift over the sum), so every indexed vertex fetch and
> every direct format with an odd stride read one word too early. The build
> works around it by expanding such draws on the CPU into a 4-byte-aligned
> direct vertex layout, where every address term is a multiple of 4. The
> workaround is on by default for Android and can be switched off in
> `Config.toml` for comparison (`[debug] aurora_deindex = false`). It costs
> CPU time in races; a GPU-side formulation that avoids the miscompile is the
> next thing to try. The compute-shader reproduction that pins the fault
> ships with the build (`[debug] aurora_gx_debug = 2048`).

---

## Rules of the Track

This repository contains **no game data and never will**: no disc image, no
`main.dol`, no `StaticR.rel`, no asset, no ghost, no translated game code.
Nothing here helps you obtain any of that.

**You bring:**

- **Your own Mario Kart Wii PAL disc (`RMCP01`)**, dumped from your own copy,
  as ISO, WBFS, RVZ, WIA, GCZ, CISO or NKit. The dump is checked against the
  game ID and the hashes of `main.dol` and `StaticR.rel` that WiiCompiled pins;
  a different game, region or disc revision is refused.
- Optional: a **Retro Rewind 6** folder containing `Binaries\Code.pul`. The
  builder's **Get / update Retro Rewind** button (`tools\Get-RetroRewind.ps1`)
  downloads it from the Retro Rewind update server and keeps it current; a
  folder that WheelWizard manages works just as well.

> **Blue shell warning.** Every build carries your translated game code and
> is for your headset only. Do not share APKs. Share this repository instead;
> everyone races on their own disc.

---

## Starting Grid: what your PC needs

| Part | Requirement |
|---|---|
| Windows | Windows 10/11 x64 |
| Memory | **16 GB RAM** (the final link needs close to 15 GB; the scripts limit parallelism for that reason) |
| Disk | about 40 GB free |
| Android SDK | **NDK 28.x**, **build-tools 35.0.0**, **platform android-34**, the **CMake** package (brings CMake and Ninja) and `platform-tools` (adb). Android Studio's SDK Manager installs all of them |
| Tools | a JDK 17 or newer (`javac`), Git for Windows, .NET SDK 8 or newer |
| Headset | a Quest 3 in developer mode, connected over USB with USB debugging allowed |

> **Under construction.** Work is in progress to make the build faster and
> to get it running on lower-spec hardware (less memory for the final link,
> fewer parallel jobs on smaller machines). Until then, the numbers above are
> what the build was verified with.

---

## Grand Prix: building the APK

### Lap 1: line up

1. **Get the tools.** Download `MKW-Quest3-Port-<version>.zip` from the
   **Releases** page (builder, scripts, patches and Android packaging files,
   nothing else), or clone this repository. Both work the same way.
2. **Make a folder for the port** and unpack the zip into it, for example
   `C:\MKW-Quest`. Rules for the path: no spaces, not inside the Android SDK,
   not inside a cloud-synced folder (OneDrive, Dropbox). After unpacking you
   should see `Quest-Builder.cmd`, `tools\`, `patches\` and `android\` in it.
3. **Have your disc dump ready** somewhere on the PC, for example
   `C:\MKW-Quest\disc\mkw.wbfs`. It is only read, never copied into the port
   folder.

### Lap 2: build

4. **Double-click `Quest-Builder.cmd`.** The window has five fields:
   - *Your Mario Kart Wii disc image (PAL)*: the dump from step 3.
   - *Retro Rewind 6 directory (optional)*: leave it **empty** the first time;
     the button in step 6 fills it in.
   - *Android SDK*: found automatically when Android Studio installed it;
     otherwise the folder that contains `ndk\`, `build-tools\` and
     `platforms\`.
   - *Work directory (created here)*: a **new, empty** folder with about 40 GB
     free, for example `C:\MKW-Quest\workspace`. Everything the build creates
     (the WiiCompiled clone, your extracted disc, the translated code, the APK)
     lands there.
   - *BuildWorkspace (legacy)*: leave it empty.
5. **Check the list.** Every required line must say OK; fix what is missing
   (the *Where* column says what was looked for). The optional lines may stay
   red for now.
6. **Click "Get / update Retro Rewind"** (optional, needed for online play).
   It downloads Retro Rewind (about 1.8 GB) into `RetroRewind6` next to the
   work directory, fills in the field, and the line *Retro Rewind up to date*
   turns green.
7. **Click "Build APK".** First time: an hour or more (clone and patch
   WiiCompiled, build the translator, validate and extract your disc,
   translate the game, native build, package). The log ends with
   `APK: <work directory>\android\out\mkw-quest.apk`. Every step is skipped
   when its result already exists, so a failed run continues where it
   stopped: fix the cause and click again.

### Lap 3: race

8. **Connect the Quest** (developer mode, USB debugging allowed on the
   headset), tick *Push game data to the headset* and click
   **"Install on Quest"**. The first push copies several GB of game data to
   `/sdcard/MKW`; later installs without the tick only replace the app.
9. **Start the app** from the headset's app library under *Unknown sources*.

> **Item box.** Updates later are short: **Get / update Retro Rewind**, then
> **Build APK** (minutes), then **Install on Quest** without the tick.

### What the buttons do

- **Build APK** clones WiiCompiled at the pinned commit into the work
  directory, applies the patches, builds the translator, validates and
  extracts your disc, translates the game (the long step, plus the native
  build), and writes `android\out\mkw-quest.apk` inside the work directory
  (the APK carries the translated game code, so it never lands in the port
  folder or in this repository).
- **Install on Quest** installs the APK over adb, grants the storage
  permission and, with the checkbox ticked, pushes the game data.

The same pipeline runs from a terminal:

```powershell
.\tools\Invoke-QuestPipeline.ps1 -Workspace D:\mkw-quest-work -DiscImage D:\dumps\mkw.wbfs `
    -RetroRewindDirectory "$env:APPDATA\CT-MKWII\RetroRewind\RetroRewind6" -Install -PushAssets
```

Start the app from the headset's app library (unknown sources). Starting it
with `adb shell am start` is not useful: Horizon OS pauses the panel at once.
Logs: `adb logcat -s mkw`.

---

## Pit Lane: what happens under the hood

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

---

## Pit Stop: updating Retro Rewind

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

---

## Tuning: switches without rebuilding

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
| `tools/` | pipeline scripts, the GUI, the release packer |
| `.github/workflows/` | the release workflow: a tag `v*` packs the release archive and publishes it |
| `images/` | screenshots from the headset for this README |

Real paths to your installation go into `local.paths.md`, which is
git-ignored.

---

## Final Standings: license

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

<pre align="center">
   ______________________________________________________________
  |  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  |
  |    ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##  ##    |
  |______________________________________________________________|
                         FINISH
</pre>
