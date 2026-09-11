# Third-Party Notices

This project is a thin layer over WiiCompiled. Every build made from it links
the components below; the licenses are those of the upstream projects and are
reproduced in their source trees (the WiiCompiled work copy carries a complete
`THIRD-PARTY-NOTICES.md` of its own, which governs everything not listed here).

## WiiCompiled — `patchzyy/Wiicompiled`

- License: GPL v3
- Use: static recompilation (translator, runtime, installer, Retro Rewind integration)
- Consequence: every distributed build of this project is GPL v3 as well.

## aurora — `encounter/aurora`

- License: MIT
- Use: GX compatibility layer on WebGPU (Dawn), SDL3 application layer. Embedded in the WiiCompiled work copy; this project patches it for Android.

## Used on the build host

| Component | License | Role |
|---|---|---|
| nodtool (`encounter/nod`, v2.0.0-alpha.10) | MIT or Apache-2.0 | reads and extracts the user's own disc image (ISO/WBFS/RVZ/...); downloaded once by `tools/Import-DiscImage.ps1` if the WiiCompiled toolkit is not installed |

## Linked into the Android build

| Component | License | Role |
|---|---|---|
| Dawn / Tint (`google/dawn`, prebuilt by `encounter/dawn-build`) | BSD-3-Clause | WebGPU implementation, Vulkan backend on the Quest |
| OpenXR loader (`KhronosGroup/OpenXR-SDK`, release-1.1.63) | Apache-2.0 (most files Apache-2.0 OR MIT) | finds the Horizon OS OpenXR runtime and provides the `xr*` entry points; shipped as `libopenxr_loader.so` in the APK |
| SDL 3 | zlib | window, input, audio, Android activity (`SDLActivity`) |
| libco (`higan-emu/libco`, vendored by WiiCompiled) | ISC (`valgrind.h`: BSD-style) | guest thread fibers on Android |
| zstd | BSD-3-Clause (election by WiiCompiled) | pipeline cache compression |
| libpng, zlib | libpng / zlib | textures, Android system zlib |
| FreeType | FTL (election by WiiCompiled) | overlay text |
| fmt, xxHash, Abseil, magic_enum, toml11, pugixml, Crypto++, Dear ImGui, Tracy | MIT / BSD-2 / Apache-2.0 / MIT / MIT / MIT / BSL-1.0 / MIT / BSD-3 | as used by the runtime and aurora |
| Android NDK `cpufeatures` | Apache-2.0 | Crypto++ CPU detection on Android |

### Vendored inside the OpenXR loader

The loader is not prebuilt: the build fetches the `release-1.1.63` source
archive of `KhronosGroup/OpenXR-SDK` and verifies it against a pinned SHA-256
before compiling it, so what ends up in the APK is exactly the tagged upstream
tree. That tree compiles the components below straight into
`libopenxr_loader.so`, which makes their licenses part of every build:

| Component | License | Role |
|---|---|---|
| jsoncpp 1.9.6 (`src/external/jsoncpp`) | Public Domain or MIT, at the user's choice | parses the runtime and API-layer manifest JSON |
| jnipp (`mitchdowd/jnipp`, `src/external/jnipp`) | MIT | JNI helper, Android-only part of the loader |
| android-jni-wrappers (`src/external/android-jni-wrappers`) | BSL-1.0 | reads the installed runtime through the Android content provider |

`src/external/sanitizers-cmake` (MIT) is in the archive as well but only ever
runs on the build host as CMake code; nothing of it is linked.

## Not included

- Game data of any kind: no disc image, no `main.dol`, no `StaticR.rel`, no
  assets, no ghosts, no translated game code. Users build from their own copy.
- Third-party texture packs.
