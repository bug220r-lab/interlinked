# Reviewed deployment helpers

These files support the existing MSI / THIS-MACHINE installation. They are not a portable installer for arbitrary computers. Private packages, inventory reports, presets and font binaries are supplied separately and must not be committed here.

## MAGIX presets and fonts

`Check-MAGIX-Presets-and-Fonts.cmd` runs `Collect-MAGIX-Inventory.ps1` as the current user. It lists file metadata in bounded MAGIX locations and registered font names. It does not follow reparse directories, read project payloads, change settings or install fonts. Unavailable/skipped roots remain visible in its report.

`Build-MAGIX-Preset-Definition-Index.py` builds a private comparison index from preserved inventory snapshots and bounded, read-locked definition files. `Compare-MAGIX-Preset-Definitions.ps1` verifies that index's pinned hash and compares the existing local definitions on THIS-MACHINE. It reports matches, missing files, differences and read errors. It never applies a replacement. Large media and movie-template bundles are outside this definition hash comparison.

`Import-MAGIX-Presets.ps1` and `import_magix_presets.py` apply an explicitly prepared private package containing 24 custom presets and the four exact League Gothic styles used on MSI. The manifest hash, interpreter dependencies, target computer, Jude account SID, application version, allowed preset locations and font hashes are checked. MAGIX must be closed. Applying requires UAC approval because the original content folders are under Program Files and the fonts are registered for all users.

The importer adds absent files only. An identical file can be skipped; a different existing file stops preflight. It does not overwrite presets, edit projects, delete files, reset font caches or change services. Receipts preserve partial additions if an operation fails. A successful receipt confirms file hashes, font registration and Windows loading, not visual rendering inside MAGIX.

The package uses the existing MSI font files to preserve their exact version. The League Gothic license is included with that private package. Other font families found in historical project font tables are reported separately; they are not silently substituted or installed by this importer.

## GUI publication

`Publish-Interlinked-GUI.ps1` verifies the preceding deployment hashes and the unchanged launcher/index pins, then acquires the GUI instance lock before replacing its allowlisted app files. Existing versions are retained as backups. Finish the MAGIX session and stop INTERLINKED before publishing; closing its browser tab alone does not stop its service.
