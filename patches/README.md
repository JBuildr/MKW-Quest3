# Patches for the Quest port

The port changes files that do **not** live in this repository: the WiiCompiled
work copy (`<work directory>`, see `local.paths.md`) and the translator source
tree. So that the work survives the loss of a work copy, the changes are kept
here as patches.

They contain **no game data**: only CMake, C++ and C#.

| File | Target | Contents |
|---|---|---|
| `0001-android-buildworkspace.patch` | work copy | platform gate, `-mcpu=native`, aurora/Dawn for Android, zstd include, Crypto++/cpufeatures, chrono type in `audio.cpp`, Android entry point in `main.cpp` |
| `0002-translator-target-os.patch` | translator @ `e6f9b21` | `--target-os`, so the generated assembly speaks ELF instead of PE/COFF |
| `0003-quest-adreno-workaround.patch` | WiiCompiled `e6f9b21` + `0001` + Fix AA | The **Adreno 740 workaround**. Every indexed draw is expanded on the CPU into a 4-byte-aligned direct vertex layout (`command_processor.cpp`, `gx.hpp/.cpp`), the vertex shader reads direct attributes word by word with constant shifts (`shader.cpp`), vertex staging 16 MiB and `align_verts` (`gfx/common.*`); on by default for Android, `[debug] aurora_deindex = false` switches it off (`runtime_config.h`, `main.cpp`, `shader_info.*`). Pure-direct draws with an odd stride are repacked the same way (menu videos, `shader_info.*`, `command_processor.cpp`); compute-shader reproduction of the driver fault for the bug report (bit 2048, `gfx/common.cpp`). Also: uniform matrices as vec4 rows, `[debug]` switches (`gpu.cpp`), `cachedRange` reset, JNI guard for guest fibers (`aurora_events.h`, `fiber_manager.h/.cpp`, `settings_overlay.cpp`, Fix AB) |

`0003` is generated against WiiCompiled `e6f9b21` **with `0001` and Fix AA
applied** (1 MiB fiber stack, a text replacement) and verified with
`git apply --check` against exactly that base (last on 2026-09-08).
The order is therefore `0001`, Fix AA, then `0003`;
`tools/New-StandaloneWorkspace.ps1` and `tools/New-QuestWorkspace.ps1` do
exactly that. When a patch in this folder changes after a work copy was made,
`New-StandaloneWorkspace.ps1` resets the files it touches to the pinned
commit, restores what the earlier patches did to them, and applies the new
version (Fix AF).

## Applying by hand

    # work copy (from its root); the scripts in tools/ do the same with
    # git apply --ignore-whitespace
    patch -p1 --binary < patches/0001-android-buildworkspace.patch
    # then Fix AA (see tools/New-StandaloneWorkspace.ps1), then:
    patch -p1 --binary < patches/0003-quest-adreno-workaround.patch

    # translator (worktree at e6f9b21)
    git apply patches/0002-translator-target-os.patch

`0001` was generated with `diff --strip-trailing-cr`: the original files in
the work copy have CRLF line endings, the edited versions LF. That is a side
effect of editing, not an intended change, and harmless for CMake and Clang;
without `--strip-trailing-cr` the patch would be 95 % line-ending noise.
When applying to a CRLF tree, use `--binary` and, if needed,
`--ignore-whitespace`.
