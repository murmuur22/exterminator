# Native Debian deployment — v0.2.0

## Scope and qualification

Supported target: **Debian 13 amd64 (x86-64), system Python 3.13, systemd**. This is a native Gunicorn release, not Docker. No in-app updater, built-in accounts, or database migration framework is included. `pyproject.toml` is the canonical application version; the UI, health response, and release manifest derive from it.

Debian 13 amd64 installation, hardened systemd operation, reboot persistence, synthetic next-version update/paired rollback, and failed-health recovery were executed in a disposable local VM. A separate real local Relay Gateway/browser proof passed against those installed services. See [qualification evidence and limits](https://github.com/murmuur22/exterminator/blob/main/docs/qualification.md). This is not evidence of a live deployment or verification of the operator's DNS/TLS/Tailscale configuration.

Only these paths/identities are managed:

| Location | Ownership / purpose |
| --- | --- |
| `/opt/exterminator/releases/VERSION-HASH12/` | root-owned code and isolated per-release venv; service cannot write |
| `/opt/exterminator/current` | atomic relative symlink to a verified release |
| `/opt/exterminator/managed.json`, `transaction.json` | root-only ownership marker / incomplete-transition record |
| `/etc/exterminator/config.json` | root:exterminator, 0640; containing directory 0750 |
| `/var/lib/exterminator/reports.sqlite3` | exterminator:exterminator, 0600; directory 0700 |
| `/var/backups/exterminator/SNAPSHOT/` | root-only, paired database/config and checksummed metadata |
| `/etc/systemd/system/exterminator@.service` | root-owned template; admin and submission instances only |
| `exterminator` user/group | dedicated system identity; `/nonexistent` home, `/usr/sbin/nologin`, no supplementary groups |

The controller refuses pre-existing installation paths on first install, even empty directories, and refuses an existing account/group or conflicting units/drop-ins. Updates require the ownership marker, matching identity, unchanged managed unit, verified release, and safe paths. It never adopts an unrelated service or edits firewall, network, SSH, Relay, Journalmax, or Tailscale configuration. It does not uninstall or prune anything automatically. Do not grant the service sudo privileges or reuse its identity for another service.

## Build on the development machine

Python 3.11+ and `uv` are needed for the builder, not for the installed service. From the repository:

```sh
uv sync --locked
uv run pytest -q
node --check static/app.js
node --check static/submission.js
uv run python deploy/build.py --wheels
```

Outputs go to ignored `artifacts/release/`:

- `exterminator-0.2.0-debian13-amd64.tar.gz` and `.tar.gz.sha256`
- `manifest.json` (also inside the archive)
- `exterminatorctl.py` and `exterminatorctl.py.sha256` (standalone stdlib controller)

`--wheels` downloads hash-checked **CPython 3.13 manylinux2014 x86-64 or universal** wheels from public PyPI, even when building on a Mac. It does not bundle Mac native wheels. All runtime packages must have binary wheels; no dependency build scripts run at install time. Without `--wheels`, installation downloads pinned, hash-checked binary packages from `https://pypi.org/simple`. With wheels, install uses `--no-index` and the bundled wheel directory, with no network fallback.

The archive has an explicit file allowlist: application source, templates/static assets, production requirements, metadata, controller/unit/example config, and operator docs. No reports, private planning notes, artifacts, tests, dev dependency wheels, or venv are copied. The full development `uv.lock` is not shipped; the hash-checked `requirements-production.txt` is. `pyproject.toml` retains development metadata, but those dependencies are not installed.

The builder verifies that the production lock matches `uv.lock`. After deliberately changing dependencies/version, regenerate and review both:

```sh
uv lock
uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file requirements-production.txt
```

Archives use deterministic file order/metadata. Hashes are integrity checks, **not signatures**: obtain the expected package and controller SHA-256 through a trusted channel. Never approve an arbitrary downloaded controller merely because it supplies its own checksum. The controller reads/hash-checks the archive once before processing it, rejects traversal, duplicates, symlinks/hardlinks/devices, and checks every payload hash against the manifest. It never invokes `tar -x` as root.

## Debian prerequisites and first install

On the **explicitly authorized target only**, install:

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends python3 python3-venv ca-certificates systemd systemd-sysv passwd util-linux sudo openssh-server curl
```

`passwd` supplies useradd/groupadd; `util-linux` supplies runuser. `openssh-server`/sudo are for operator access; curl is for manual checks. On a preconfigured shared host, use its existing approved SSH/access setup instead of changing it. No compiler, pip apt package, uv, SQLite CLI, Node, or browser is required on the target. Debian's venv supplies pip. Ensure adequate space for two release environments and database snapshots, stable configured private addresses if used, and unoccupied ports 8741/8742.

Example SSH workflow (replace `AUTHORIZED_HOST`, and use only a separately approved host). Copy the archive, its checksum, controller, and controller checksum into the operator's private home directory with scp. Compare both hash strings to the trusted build-machine values **before** executing anything with sudo:

```sh
ssh operator@AUTHORIZED_HOST
sha256sum exterminator-0.2.0-debian13-amd64.tar.gz exterminatorctl.py
sudo install -o root -g root -m 0600 exterminatorctl.py /root/exterminatorctl.py
sudo /usr/bin/python3 -I /root/exterminatorctl.py install \
  --package "$HOME/exterminator-0.2.0-debian13-amd64.tar.gz" \
  --sha256 TRUSTED_64_CHARACTER_LOWERCASE_SHA256
```

Do not overwrite an unrelated `/root/exterminatorctl.py`; choose another private root-owned operator filename if it exists. `-I` isolates the root controller from Python environment/user-site injection. The checksum argument is mandatory; it is never inferred from the neighboring checksum file. `verify-package --package ... --sha256 ...` can validate an archive without root on the development machine.

Omitting `--config` installs the safe example configuration: both surfaces bind `127.0.0.1`, with only `127.0.0.1` accepted as socket peer, and framing denied. To install a reviewed configuration, add `--config "$HOME/exterminator-config.json"`. Configuration is JSON, not a shell environment file. Unknown keys, networks/wildcards, public binds, and invalid origins are refused. Config and database paths are fixed outside the release tree.

The installer stages files and a venv, then validates configuration as the service account, stops both units, checks ports, initializes the database as the service account, atomically activates the release, enables/starts both units, and verifies each `/healthz`. Root runs only the trusted controller, venv creation, and hash-checked pip installation; application/schema initialization does not run as root. Controller invocations serialize with an advisory lock on `/opt` (no world-writable lockfile).

```sh
sudo systemctl status exterminator@admin.service exterminator@submission.service
curl --fail http://127.0.0.1:8741/healthz
curl --fail http://127.0.0.1:8742/healthz
sudo /usr/bin/python3 -I /root/exterminatorctl.py status
```

Health returns only `ok`, surface (`admin` or `submission`), and version. It opens/queries the report database and checks the supported schema version. DB failures return generic 503 JSON, not SQL, report contents, or tracebacks. The submission service never registers admin list/edit/export routes.

## Relay gateway and the network boundary

**There are no application accounts.** Different ports do not authenticate anyone. Relay must authorize the admin target only for admins and the submission target for intended reporters; backend ports must not be reachable by ordinary LAN/Tailnet clients. Framing permissions and `X-Exterminator: 1` are not authentication. All processes able to originate from an allowed host address can access that surface; this is not per-process Relay authentication. Service accounts share the host kernel and are not VM isolation.

For Relay and Exterminator on the **same VM**, keep both services on loopback. Relay's fixed-target Gateway supports these loopback upstreams. Set only the exact Relay HTTPS desktop origin to permit embedding:

```json
{
  "admin": {"relay_origin": "https://relay.example"},
  "submission": {"relay_origin": "https://relay.example"}
}
```

The omitted bind/peer settings retain `127.0.0.1`. No LAN/Tailnet backend listener is needed. A separate, explicitly reviewed private-address deployment is also supported, but is not the recommended same-VM arrangement. For that alternative, opt in to nonloopback and allow only exact gateway socket peers, for example (synthetic address):

```json
{
  "admin": {
    "bind": "192.168.50.10",
    "allow_nonloopback": true,
    "allowed_peers": ["192.168.50.10"],
    "relay_origin": "https://relay.example"
  },
  "submission": {
    "bind": "192.168.50.10",
    "allow_nonloopback": true,
    "allowed_peers": ["192.168.50.10"],
    "relay_origin": "https://relay.example"
  }
}
```

Ports remain fixed: admin 8741, submission 8742. Binds support `127.0.0.1`, RFC1918 IPv4, or CGNAT `100.64.0.0/10` addresses with the explicit opt-in. No wildcard/public/IPv6 bind is supported in this narrow release. Peers are individual IPv4 literals, never CIDRs, DNS names, `*`, or “all LAN.” The bind address must be in its allowlist for same-host health checks. A separate gateway address can be added only as an explicit reviewed peer; do not infer it from browser or proxy headers.

Same-host TCP connections to an assigned private address normally use that same address as their source. The controller explicitly binds health probes to it. Verify the actual gateway socket peer in your isolated integration test; if routing selects another source, diagnose routing rather than broadening access. The installer does not configure network rules. Tailscale rules alone do not prevent direct ordinary LAN traffic. Validate the boundary with a second client and the actual grants before making either surface available.

Relay target requirements:

- Recommended same-VM upstream URLs: `http://127.0.0.1:8741` and `http://127.0.0.1:8742`. For an explicitly configured private bind, substitute that bind address. Preserve upstream Host equal to the exact `IP:port` authority, **not** the Relay hostname. Wrong/missing port or the other surface's authority is rejected.
- Add **`x-exterminator`** to each target's `requestHeaders`; preserve its browser-supplied value `1`. Relay already forwards JSON `content-type`; do not add it to the custom-header list. Do not inject a blanket mutation header into requests that lack it.
- Keep browser `Origin`, `Sec-Fetch-Site`, `X-Forwarded-*`, and `Forwarded` out of the target's forwarded browser headers. No-Origin gateway requests are supported; direct requests that include Origin must match the direct HTTP same-origin authority, including port. A Relay HTTPS Origin is **not** accepted as a mutation-origin exception.
- The application and Gunicorn ignore forwarded IP/host/scheme headers. `REMOTE_ADDR` must be the real TCP peer, not middleware-rewritten identity. Do not add ProxyFix.
- Set each surface's `relay_origin` to exactly one HTTPS origin, no trailing slash/path, credentials, or wildcard. This emits `frame-ancestors https://relay.example` (or an explicit HTTPS port) in CSP. Empty means `'none'`. Other CSP restrictions remain. This only permits embedding; it does not grant access.

These are operator instructions, not changes to a live Relay target. Test no-Origin forwarding, custom-header forwarding, exact Host, response CSP, nested framing if applicable, and admin denial for a non-admin account using the real gateway before approval.

## Updates, configuration changes, and explicit rollback

Copy and trust-check the new archive/controller as above. Use the reviewed new controller if its interface changed. Then:

```sh
sudo /usr/bin/python3 -I /root/exterminatorctl.py update \
  --package "$HOME/exterminator-0.2.0-debian13-amd64.tar.gz" \
  --sha256 TRUSTED_64_CHARACTER_LOWERCASE_SHA256
```

Updates stage/install dependencies **before stopping** a healthy current service. The release ID is `version-first12ofPackageSHA256`; different builds of the same version are distinct. An exact already-staged, complete, verified release can be reused; unrelated, changed, symlinked, or incomplete paths cannot. To change only configuration, reuse the same package and add `--config "$HOME/reviewed-config.json"`. Do not edit the active file first: passing a candidate preserves the previous active config in the paired snapshot.

After stopping both units, the controller saves a consistent SQLite backup plus the old config and old release ID under `/var/backups/exterminator/`. Then it initializes/checks the current layout, switches the relative `current` symlink atomically, restarts both surfaces and checks database/surface/version health. It retains prior releases and snapshots. No in-place code edit, automatic migration, or automatic data rollback occurs. Schema version 0 is adopted only for the recognized original layout (or an empty database); version 1 is current. Newer/unknown layouts are rejected.

On failure after stopping, the controller attempts to leave both surfaces stopped, retains `transaction.json`, and prints the snapshot path if one was created. An update refuses an incomplete transaction until explicitly recovered. There is **no silent destructive rollback**. Read the failure stage, service journal, and transaction record; do not blindly retry or reboot as a repair. Already enabled units may start the selected release on reboot. If recovery is deferred, explicitly stop/disable these two managed instances until the operator is ready.

```sh
sudo journalctl -u exterminator@admin.service -u exterminator@submission.service --no-pager
sudo /usr/bin/python3 -I /root/exterminatorctl.py rollback \
  --snapshot SAVED_SNAPSHOT_ID --restore-data
```

Rollback requires an intact checksummed snapshot containing the **matching database and config**, plus its retained verified release/venv. `--restore-data` is explicit acknowledgment that reports/config after that snapshot will no longer be current. The controller first saves the current database/config into a new snapshot, then restores the selected pair, discards only stopped SQLite sidecars, switches code, and health-checks both surfaces. It does not merge reports or pretend a code-only downgrade is safe. Preserve the new pre-rollback snapshot to recover later reports manually. Failed/corrupt current-database backup prevents replacement; use the manual preservation procedure rather than forcing the automated path.

No scheduled backup or off-host retention is provided. Backups contain private reports; copy them to an approved private backup destination under a separate policy. Keep enough free disk and monitor it. The controller bounds archive expansion and individual file reads to 100 MiB; a database exceeding that current controller limit requires manual offline backup/recovery rather than an automatic update. The runtime itself does not impose that database size limit. Do not delete the selected/current release or any rollback dependency.

## Offline and manual recovery

Normal `rollback` above is **offline**: retained venv + snapshot are sufficient, no PyPI or package download is used. Wheel-bundled install/update is also offline after Debian apt prerequisites are present. Save the trusted controller, original archive/checksum, retained releases, and paired backups somewhere the operator can access without the gateway. If a retained venv is damaged, do not activate it: rebuild it from the original verified archive and bundled wheels in an isolated new release path or use a separately reviewed recovery procedure.

If a crash, initial-install failure, corrupted DB, or ownership check prevents the normal controller from operating:

1. Use the host console or approved SSH, not the application. Stop **only** `exterminator@admin.service` and `exterminator@submission.service`; verify both are inactive and no processes of those units remain. If an unrelated listener occupies the port, stop here—do not kill it.
2. Before changing anything, make a new root-only rescue directory. Preserve `/var/lib/exterminator/` including every SQLite sidecar, `/etc/exterminator/`, the `current` symlink, `managed.json`, and `transaction.json`. Preserve even a corrupted database verbatim. Example after verifying these paths are the managed ones:

   ```sh
   sudo systemctl stop exterminator@admin.service exterminator@submission.service
   sudo systemctl is-active exterminator@admin.service exterminator@submission.service
   sudo install -d -m 0700 /var/backups/exterminator/MANUAL_UNIQUE_RESCUE
   sudo cp -a /var/lib/exterminator /var/backups/exterminator/MANUAL_UNIQUE_RESCUE/data
   sudo cp -a /etc/exterminator /var/backups/exterminator/MANUAL_UNIQUE_RESCUE/config
   sudo cp -a /opt/exterminator/current /var/backups/exterminator/MANUAL_UNIQUE_RESCUE/current
   ```

   `is-active` exits nonzero for inactive units; inspect its output rather than putting it in a blind `set -e` chain. Choose a previously absent rescue directory, and preserve the metadata files too if present. Never remove the only copy of a database or an unrelated pre-existing path/account to make the installer run.
3. Validate the chosen `snapshot.json` file hashes and recorded release ID. Validate that retained release's manifest and ownership; check every target is a regular file/directory, not an unexpected link. The trusted controller's `read_snapshot`, `verify_release`, and `validate_data_paths` helpers can be invoked from a root Python maintenance session after loading the trusted controller with `importlib.util`. Do not just extract an old tar over `current`.
4. With both services stopped, restore **both** `reports.sqlite3` and `config.json` from that snapshot, using their ownership/modes in the table above. Preserve current data first as in step 2. Remove only the stopped managed database's `-wal`, `-shm`, and `-journal` sidecars so they cannot replay newer pages into the restored DB. Restore the matching verified release's unit template if needed. Create a temporary relative symlink `releases/RECORDED_RELEASE_ID` in `/opt/exterminator/`, then rename it over `current` atomically. Never point `current` outside `releases/`.
5. Validate/init schema as `exterminator` with that release's venv, not root. Reconcile the controller ownership marker/unit checksum only after proving they refer to the dedicated identity and reviewed unit. Preserve the failed transaction record in the rescue directory before clearing it. Run `systemctl daemon-reload`; for each of `exterminator@admin.service` and `exterminator@submission.service`, inspect `systemctl show UNIT -p ActiveState --value` and run `systemctl reset-failed UNIT` only if it says `failed`. Never use a bare reset-failed affecting unrelated services. Start the two named instances. Check both `/healthz` for the recorded version/surface and check a known synthetic report before enabling unattended restart/reboot again.
6. A failed **first install** has no previous release snapshot to roll back to. Preserve partial files and inspect the exact failing stage. Repair only proven controller-owned paths/account/units with operator review, or restore a VM-level pre-install snapshot in the disposable qualification VM. The installer intentionally refuses to auto-delete/adopt partial installations or unrelated pre-existing state.

Manual recovery is deliberately not a force/skip-check switch. If ownership or snapshot provenance cannot be established, leave the service stopped and investigate.

## Runtime bounds and logs

Each surface has two synchronous Gunicorn workers, a 30-second worker/graceful timeout, backlog 64, bounded request line/header counts/sizes, periodic worker recycling, and a 32 KiB Flask body limit. Report fields retain their prototype limits and strict JSON/type/enum validation. SQLite uses a 10-second busy timeout. There is no proxy-header trust, browser CORS exception, or application rate-limiter; enforce per-user admission/rate limits at the authorized gateway. Admin list/export remain whole-inbox operations suited to this small homelab tracker; no pagination/product redesign was introduced.

Each systemd instance has a non-login identity, no capabilities, read-only system/code/config, only its data directory writable persistently, private temporary/device namespaces, syscall/address-family restrictions, 256 MiB memory cap, 100% CPU quota, 32 tasks and 1024 file descriptors. It waits on `network-online.target`; ensure the host's existing network manager correctly implements online readiness for a private bind. The dedicated identity cannot write releases or backups. Other world-readable files on a shared host remain a shared-host consideration.

HTTP access logging is disabled and Gunicorn emits only critical diagnostics; report database errors are handled without exception logging. Do not turn on request-body/SQL/debug logging for production. Use systemd unit state and `/healthz` for routine monitoring. Reverse proxies must likewise avoid logging report bodies. A health endpoint verifies a DB read and schema version, not free disk or a guaranteed future write.

## Disposable Debian qualification checklist

Use the recorded [qualification results](https://github.com/murmuur22/exterminator/blob/main/docs/qualification.md) as the baseline; repeat relevant checks for your environment and future releases:

- Install the wheel archive on clean Debian 13 amd64; verify account, root-owned release/venv, modes, exact binds, health, and both enabled units. Inspect `systemd-analyze verify` and `systemd-analyze security` output rather than inferring a score.
- Submit synthetic data through 8742, read/resolve/export through 8741, confirm all admin routes are absent on 8742. Confirm oversize input, bad Host, bad Origin, missing mutation header, and unallowed socket peer rejection.
- Reboot; verify both surfaces return the same version and synthetic data persists. Confirm service identity cannot write code/config/backups or unrelated protected data.
- Update/reapply the package (optionally with a reviewed config), add newer synthetic data, explicitly roll back to the recorded pair; confirm old data/config return and the newer data is retained in a new snapshot. Exercise a failed health transition and recover explicitly.
- Refuse wrong package hash, archive traversal/symlinks/duplicates, existing unrelated paths/accounts/units/drop-ins, changed release ownership/content, and newer DB schema. Ensure unrelated listeners/services/config are unchanged.
- Separately test private-IP same-host Relay forwarding, custom header allowlist, exact Host/CSP, grant isolation, and denial from a different peer. No live gateway changes are authorized by this checklist.

No project license has been selected. Packaging does not select one.
