# tools

Build-host scripts. They only read the user's disc image or WiiCompiled
installation and write to the work directory; none of them copies game data
into this repository.

| Script | Purpose |
|---|---|
| `Quest-Builder.ps1` | the GUI (start it with `..\Quest-Builder.cmd`) |
| `Invoke-QuestPipeline.ps1` | the whole pipeline in one call; picks the mode from `-DiscImage` |
| `New-StandaloneWorkspace.ps1` | clone WiiCompiled at the pinned commit, apply `0001`-`0003`, build the translator |
| `Get-RetroRewind.ps1` | download Retro Rewind 6 from its update server, or apply the delta updates since the installed `version.txt` (no WheelWizard needed) |
| `Import-DiscImage.ps1` | validate the user's ISO/WBFS/RVZ with `nodtool` (game ID, pinned hashes) and extract it |
| `Translate-Game.ps1` | full translation for Android (base game, optional Retro Rewind) |
| `Build-Quest.ps1` | CMake configure with the NDK, Ninja build, APK packaging (`-Product RetroRewind|WiiCompiled`; `-Jobs` defaults to one parallel compile per GB of free memory) |
| `New-QuestWorkspace.ps1` | legacy mode (source tree only): copy an installed BuildWorkspace and apply `0001` + `0003` |
| `New-ReleaseZip.ps1` | pack the release archive (builder, scripts, patches, Android files) next to the repository; `-Version v0.1.0` |
| `Regenerate-AndroidTranslation.ps1` | legacy mode (source tree only): re-run only the assembler-emitting translator steps for Android |

Standalone order (what the GUI does): `New-StandaloneWorkspace` →
`Import-DiscImage` → `Translate-Game` → `Build-Quest` → `android\deploy.ps1`.

Local discovery scripts that hard-code machine paths stay untracked (see
`.gitignore`).
