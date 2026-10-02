# Changelog

## Unreleased — local prototype 0.1.0

### Source documentation

- Added a plain-language README with real demo screenshots of both interfaces, portable local setup instructions, data/backup guidance, and the planned Relay/Tailscale access model.
- Prevented a pending admin save from dismissing a later editor session; Close/Cancel/Escape and duplicate saves are blocked until that save settles. Added a delayed-response browser regression.
- Added public testing notes and publication exclusions for runtime data, credentials, private planning notes and local design archives.
- Source publication is separate from a packaged release or production deployment; no project license has been selected.

### Separate submission listener

- Added the GeoCities-style end-user report form on loopback port 8742 (`uv run python submit.py`), separate from the admin tracker on 8741.
- Reports are saved to the same SQLite inbox. The submission app mounts no report-list, detail, edit or export routes, and returns only a success acknowledgment.
- Added submit/success/send-another states and failure handling that retains the draft. Existing services are not suggested or disclosed.
- Added route-isolation, validation, origin-boundary and two-listener Chromium tests, plus desktop/mobile/success screenshots.
- Remains local-only with no login, remote access, Relay embedding or production deployment. Separate ports are not authentication.

### GeoCities visual revision

- Replaced the modern retro treatment with tiled green wallpaper, Times typography, blue underlined report links, beveled panels and native-style form controls.
- Added original pixel bugs, homemade site badges, a site directory, and playful pest-control copy; no flashing, audio, third-party assets or fake visitor counts.
- Refined the initial modern-retro visual treatment into a genuinely old-web interface.
- Kept all report behavior, storage and local-only boundaries unchanged. Re-ran all 15 tests and desktop/mobile screenshot checks.

### Initial prototype

- Implemented standalone Flask/SQLite report capture with optional service and details, plus Bug / Feedback / Idea kinds.
- Added Inbox, In progress, Resolved, All reports, text search, and service filtering.
- Added editing and reversible status changes; resolving retains reports.
- Added JSON export of all saved reports and documented database backup/restore.
- Created original retro exterminator branding, pest emblem, work-order styling, and responsive capture/editor UI.
- Restricted prototype to local loopback; added request validation, host/origin checks, no-store responses, and a restrictive content security policy.
- Added storage/API tests and real Chromium acceptance coverage, including mobile, error recovery, and literal rendering of user-provided markup.
- Kept authentication, submission endpoint, Relay embedding, deployment, and publishing out of this prototype. No live services were modified.
