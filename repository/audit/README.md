# Read-only MAGIX media audit

`audit_media.py` reads the persisted extraction/selection and published catalog, holds the existing library reader lock, and stats only selected or referenced local paths. It does not read media/project payloads, hydrate cloud files, contact a remote host, change a catalog/cache, relink a project, or invoke MAGIX. It refuses existing output reports.

From the source workspace root, with the packaged source in `repository/audit`:

```powershell
python -B repository\audit\test_audit_logic.py
python -B repository\audit\audit_media.py --output work\private-magix-audit
```

Defaults refer to the existing private installation. Command arguments accept separately supplied library, dependency report, selection, reviewed engine and output paths. `--output` is required and must be outside the auditor's code folder and source distribution. Use an ignored local work folder or an external private directory. UNC inputs/outputs are refused. Never commit generated reports: they contain private project names and source paths.

## Deliverables

- `MAGIX-media-audit.json`: full evidence, all 2,870 historical string candidates, 43 families, 276 entrypoints, selected-file metadata, deferred records, and manual review proposals.
- `MAGIX-media-audit-gui.json`: `format: magix-media-audit-gui-v1`; project rows with qualified video/audio/image references only. Other raw counts remain in `counts.classifications`; `counts.mediaClassifications` supplies the main media view.
- `MAGIX-media-audit-summary.json`: counts and limits.
- `MAGIX-media-audit.html`: offline searchable family report. It performs no action beyond local filtering.

## GUI contract

Top level: `format`, `generatedUtc`, `catalogId`, `summary`, `scope`, `limitations`, `provenance`, `projects`, `proposedFixes`.

Each project has `id`, `name`, `health`, `status`, `summary`, `counts`, `coverage`, `entryPoints`, `warnings`, `references`, `proposedFixes` (IDs), `candidateFolderSuggestions`, and `deferredSources`. Entry-point rows retain exact reference IDs and separate per-entrypoint media counts. Do not treat backup-family totals as a current MVP's dependency list.

Reference rows use `id`, `kind`, `path`, `name`, `classification`, `status`, `role`, `qualifiedMedia`, `evidence`, `candidatePaths`, and a nullable `proposedFix` ID. Full observation and exact/resolution evidence are in the full report's global `references` array. There is no global `fixes` field: use `proposedFixes`.

Candidate paths contain `sourcePath`, `cacheRelativePath`, `cacheFolderRelativePath`, `selectedForFamily`, `libraryMatchesCatalog`, `currentClientCacheExists: NOT_CHECKED`, and `matchBasis: BASENAME_ONLY_IDENTITY_UNVERIFIED`. Join a cache-relative path only to the selected project's owned cache root. Verify actual local existence before offering a folder-open action. Do not infer a current edited cache from host catalog coverage.

Main classifications distinguish exact literal availability, supported selected aliases, installed assets, relative resolution candidates, unique/multiple unverified basename alternatives, cloud deferral and `NO_LOCAL_MATCH_IN_AUDITED_PATHS`. The last means an extracted historical reference has no located candidate, not a proven active missing clip. `activeDependency` remains unknown. Relative project-parent searches are explicitly inferred candidate bases, not established MAGIX path semantics.

All current proposed fixes are manual candidate reviews; `automaticSafe` is false. A future exact-literal addition would require separate source snapshot/hash, owned-cache and space review. Nothing in this report authorizes replacing media, editing binary MVP bytes, mapping new roots or changing the published catalog.

File IDs prove current source/published hardlink identity only. They do not prove the media version present when a project was authored, codec compatibility, current cache completeness, native reopening or playback. Keep these limits visible next to counts.
