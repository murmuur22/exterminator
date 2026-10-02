# Testing the prototype

Run from the repository root:

```sh
uv sync --locked
uv run playwright install chromium
uv run pytest -q
```

For JavaScript syntax checking, if Node.js is installed:

```sh
node --check static/app.js
node --check static/submission.js
```

## Coverage

The suite includes parameterized API cases and real Chromium workflows:

- SQLite capture, persistence through a new store instance, editing and status changes.
- Report-field type/length validation and unknown-field rejection.
- Local peer, Host, Origin, and cross-site request restrictions.
- Admin capture, reload, resolve, reopen, editing, search, filtering, and export download.
- Blocking Close/Cancel/Escape during a pending admin save, with controls restored after completion.
- Separate HTTP listeners sharing a temporary database: submit through the end-user form, then open the report in the admin UI.
- No report-list/read/edit/export endpoints or admin JavaScript on the submission listener; acknowledgments contain no stored report data.
- Draft preservation and recovery after deliberately aborted requests.
- Literal rendering of HTML-looking report titles rather than script execution.
- Responsive overflow checks at multiple widths, including 320px, and usable mobile report-title targets.

The browser tests generate screenshots in ignored `artifacts/`. The README images under `docs/images/` are reviewed copies of those screenshots. Admin screenshot records are explicitly labeled demo fixtures. No normal runtime database is used by the tests.

## Limits

Passing these checks does not establish production security, performance, multi-user editing safety, or Relay/Tailscale deployment correctness. There is no authenticated deployment, automated backup, migration system, JSON restore UI, or submission idempotency. If a response is lost after a save, retrying can create a duplicate; the form warns about this.

Browser coverage is Chromium, not Safari/Firefox or physical devices. The tests run locally; no hosted CI workflow is included in this initial source publication. No live services are contacted.
