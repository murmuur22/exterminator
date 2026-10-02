# v0.2.0 qualification

## What was actually tested

All development, packaging and testing took place on an Apple Silicon laptop. Debian tests ran in a **local, emulated Debian 13 amd64 VM**, not on a production server and not on a remote CI runner. The guest used Python 3.13 and real systemd. Dependencies were installed from the release's bundled Linux/universal wheels after Debian prerequisites were present.

### Automated source tests

**81 tests passed**, including the existing Chromium interface flows, real Gunicorn listeners, strict peer/Host/Origin validation, invalid UTF-8 refusal, database schema checks, archive/path refusal, and controller ordering tests. Controller unit tests that stub systemctl are separate from the real Debian results below.

### Debian execution

- Fresh installation created the dedicated non-login account, root-owned release/venv, separate private config/data/backup paths, and both systemd instances.
- Both instances returned matching surface/version/database health and listened only on `127.0.0.1:8741` / `127.0.0.1:8742`.
- Real process inspection confirmed service UID, zero effective capabilities and `NoNewPrivileges`. Permission checks denied service writes to code/config and access to root-only backups and neighboring private files.
- Submitted synthetic reports, edited/resolved/reopened them, downloaded exports and exercised concurrent capture. Invalid host/origin/body/Unicode input and submission-side admin routes were rejected.
- Rebooted the VM: both services started automatically and the saved reports persisted.
- Updated to an explicitly **synthetic 0.2.1 fixture**, preserving reports/config. Rolled back with the paired snapshot and confirmed reports created after the update remained available in the pre-rollback backup.
- Activated an explicitly **synthetic broken 0.2.2 fixture**: health verification failed, both services were left stopped, a new update was refused while the transaction was incomplete, and explicit paired rollback recovered the healthy installation.
- Wrong package checksums, installation over existing state, and an unmanaged template drop-in were refused without disturbing the healthy installation.
- A real connection from an unallowed loopback source was denied even with spoofed forwarded-IP headers.
- Harmless synthetic neighboring service units/configuration remained unchanged and active. These were not copies of production Relay or Journalmax.

The synthetic version fixtures are test inputs, not released application versions. Two Debian-specific installer defects were found and fixed during qualification: querying an uninstantiated systemd template and resetting failure state on unloaded fresh instances. Regression tests retain both cases.

### Relay Gateway integration

A disposable local instance of Relay's actual compiled desktop and backend used genuine enrollment, separate ordinary/admin accounts and normal app-grant APIs. Its upstreams reached the real installed Debian services through local SSH forwards.

Verified in Chromium:

- Ordinary user login, submission app visibility and admin launch denial.
- Submission through the actual embedded form; list/export endpoints absent there.
- Admin report readback, editing, resolve/reopen and actual export download with content verification.
- Revoked grants deny replay of an observed, previously successful app capability; fresh login also loses the grant.
- Logout denies the previously working admin capability.
- Browser requests stayed on the desktop and launched app gateway origins, with no page errors.

The harness observed actual launch responses through a local HTTP adapter before allowing the corresponding iframe origin; it did not fabricate identity, grants or successful responses. Device health probes were disabled through normal preferences for this gateway-only traffic check. Earlier harness selector/argument errors were corrected and the entire proof rerun successfully.

TLS used a **process-local exact-certificate SPKI exception and local hostname mapping**. This is not Safari, OS trust-store, public certificate, DNS, or live Tailscale policy qualification. No live Relay settings were changed.

### Review and release boundary

Independent read-only source review passed after the installer and malformed-Unicode fixes. This complements, not replaces, the execution evidence above. Final source export, package contents and published asset checksums are checked during release preparation.

## Remaining operator responsibilities and limits

- Configure the real Relay HTTPS origin and grant only admins the admin target. Keep backends on loopback for the supported same-VM arrangement; do not substitute Native launchers for Gateway authorization.
- Verify the real network/DNS/TLS environment and access from an ordinary user before sharing. No built-in Exterminator login is provided.
- Backups during transitions are local, not scheduled or off-host. The controller currently limits individual files, including automated database backup/restore reads, to 100 MiB.
- No power-loss transactional upgrade guarantee: a failed transition is recorded, but enabled units can start on reboot. Follow the documented explicit recovery/disable procedure rather than rebooting as a repair.
- No workload-capacity, high-availability, hostile multi-tenant or physical-device browser qualification is claimed. Emulation timings are not production benchmarks.
- No project license has been selected.
