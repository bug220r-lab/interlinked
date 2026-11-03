# MSI temperature provider

This separate package targets the reviewed MSI GS66 / Intel i7-10750H account.
It does not change THIS-MACHINE's cooler startup, TRCC, USB configuration, Ollama,
the AI node's privileges, project caches or existing tasks.

`Build-MsiSensors.ps1` requires a fresh read-only MSI identity report and exact
reviewed `HWINFO.exe` / `HWiNFO64.dll` hashes. Build in a new private directory;
vendor payloads and generated binaries stay outside Git. Run
`Test-MsiSensors.ps1 -BuildDirectory <candidate>` before publication. Its pure
fixtures and wrong-host refusals never launch setup or sensors.

The frozen `INTERLINKED.MSI.Sensors.Setup.exe` embeds only the exact publisher and
two vendor files. Run it explicitly on MSI as its reviewed user and approve the
Windows administrator prompt. Setup creates the protected
`C:\Program Files\INTERLINKED MSI Sensors` payload and
`C:\ProgramData\INTERLINKED\MSI-Sensors` telemetry directories. Only SYSTEM and
Administrators can write; ordinary users can read. Setup owns one fixed elevated
interactive-logon task, `INTERLINKED MSI Sensor Telemetry`, with at most three
one-minute failure retries. Existing paths/tasks are refused, not adopted or
overwritten. An active sensor publisher is preserved and blocks setup. The
explicit `--install` invocation performs the same fixed installation without
message boxes and emits a bounded result; it does not request or acquire
privileges itself. Its executable still has the same administrator manifest.
Successful installation writes protected `setup-result.json` with exact payload
hashes, protected-ACL verification and the read-back owned task definition.
Task registration suppresses the scheduler's automatic principal ACE and leaves
the task disabled until its protected Admin/SYSTEM-only security descriptor and
fixed definition read back exactly. No registration or readback failure can
enable the task.

The publisher accepts no arguments and launches only the pinned sensor helper in
a kill-on-close Windows Job Object. It creates its own protected vendor sensor
map; a pre-existing map or publisher prevents ownership. The Intel `CPU Package`
temperature must come from the exact CPU #0 / i7-10750H / DTS group, matching the
vendor's group-name casing without changing the sensor identity. ACPI, core,
other CPU models and AMD readings are not substitutes. A map that stops changing
for five seconds yields unavailable readings. Output is atomically replaced,
bounded JSON with source `reviewed-sensor-publisher` and per-field timestamps.
The node independently reads NVIDIA GPU temperature/load/VRAM for its verified
primary GPU; the publisher does not guess that GPU's identity.

`descriptor.json` identifies the exact publisher hash, MSI SID, fixed paths,
sensor mapping and vendor hashes. `temperatures.json` contains only bounded
temperature telemetry. Neither file includes paths to projects, credentials or
conversation content. The read-only AI telemetry operation never starts or
elevates this provider.

The retained setup's explicit `--disable` action verifies the exact protected
payload, descriptor and owned task before disabling/stopping only that task.
It retains files and leaves unrelated publishers/tasks untouched.

Synthetic parser tests establish refusal behavior, not MSI sensor availability.
Installed byte/ACL/task readback and fresh real CPU-package/NVIDIA GPU readings
are required before live MSI temperature acceptance is marked complete.

## CPU-only replacement

`MsiCpuOnly.cs` replaces the vendor process with a fixed Intel package-DTS reader
for MSI's exact i7-10750H / processor ID `BFEBFBFF000A0652`. Its only register
requests are `IA32_TEMPERATURE_TARGET` (`0x1a2`) and
`IA32_PACKAGE_THERM_STATUS` (`0x1b1`), on logical CPU zero. Invalid driver reads,
an unset package-valid bit, or an implausible temperature target produce an
unavailable sample. Constant valid temperatures remain fresh when reread.
There is no EC, motherboard, battery, fan, GPU or USB polling, child process,
register-write operation, configurable device path or arbitrary MSR argument.

The package embeds only the signed IntelMSR module from LibreHardwareMonitor
**v0.9.6**, SHA256
`ed41a0d0de082f4668a9caccb9681ebbad97a8ed3452c41918dda1eb454b77a9`.
The wire protocol and temperature decoding follow that project's MPL-2.0 source;
see `MsiCpuOnly-LICENSE.txt` and these upstream source files:

- https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.6/LibreHardwareMonitorLib/PawnIo/PawnIo.cs
- https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.6/LibreHardwareMonitorLib/Hardware/Cpu/IntelCpu.cs
- https://github.com/namazso/PawnIO.Modules (official signed module distribution)

The separately installed dependency is official signed **PawnIO 2.2.0** from
https://github.com/namazso/PawnIO.Setup/releases/tag/2.2.0. Its installer SHA256 is
`1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032`.
Use its signed default edition and administrator-only device access. The build
script never installs a driver. Driver setup and actual MSI readings are separate
acceptance steps; do not replace the driver with an unsigned/unrestricted build
or relax Windows security settings.

Build in a fresh private folder with `Build-MsiCpuOnly.ps1 -OutputDirectory ...
-IntelModule ... -IdentityReport ...`. The report must identify the reviewed
MSI account and CPU and be less than 24 hours old. The publisher supports:

- `--self-test`: pure register-decoding, module-integrity and task-action checks;
  no hardware or privilege requirement.
- `--probe`: exactly fifteen samples on the reviewed MSI administrator account,
  without changing the production telemetry file or scheduled task.
- No arguments: protected production location, descriptor and executable hash
  must match; a protected exclusive file lease prevents overlapping publishers.
  The publisher atomically replaces the existing bounded JSON once per second.
- `--disable`: verifies the installed CPU-only identity and owned task before
  disabling/stopping that task. Empty Task Scheduler arguments may be `null` or
  an empty string; both represent the same fixed action.

`Migrate-MsiCpuOnly.ps1` performs an explicit reviewed migration. It requires
exact candidate and predecessor executable/descriptor hashes, the existing
disabled owned task, protected staging files, unchanged vendor bytes, and the
retained reviewed predecessor setup. It preserves task action, identity and
security, creates protected backups and a rollback script, starts the existing
task and requires fresh telemetry. Failure leaves the task disabled and attempts
to restore the predecessor bytes. Retained vendor files are rollback material;
the old vendor setup's `--disable` no longer manages a CPU-only installation.

The new descriptor retains the node's existing format, account, fixed paths and
`reviewed-sensor-publisher` provenance, while recording its CPU-only backend and
module hash. No node or THIS-MACHINE application replacement is needed. Existing
five-second per-field expiry and independent NVIDIA GPU telemetry remain active.

Actual installed hashes, task readback, probe results, stop/expiry/restart checks,
bounded power observations and human cooler acceptance belong in private release
receipts. They do not establish indefinite power stability, automatic startup
after a future reboot, or sleep/resume recovery.
