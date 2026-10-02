"""Real Chromium acceptance test. Demo data lives only in pytest's temp directory."""
import threading
from pathlib import Path
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect
from app import create_app


def test_browser_capture_triage_search_export_mobile(tmp_path):
    server = make_server('127.0.0.1', 0, create_app(tmp_path / 'browser.sqlite3'), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1020}, device_scale_factor=1)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            response = page.goto(f'http://127.0.0.1:{server.server_port}')
            assert response.status == 200, 'Inbox interface is not implemented'
            expect(page.get_by_text('No reports yet.')).to_be_visible()
            expect(page.get_by_text('Welcome to our little corner of the infestation.')).to_be_visible()
            assert 'wallpaper.svg' in page.locator('body').evaluate('(n) => getComputedStyle(n).backgroundImage')
            assert page.locator('body').evaluate('(n) => getComputedStyle(n).fontFamily').startswith('"Times New Roman"')
            title = page.get_by_label('What happened?')
            title.fill('[Demo] Jellyfin playback stalls after seeking')
            page.locator('#capture-form').get_by_label('Service / project').fill('Jellyfin')
            page.locator('#capture-form').get_by_label('Details').fill('Demo report, not a verified service issue. Playback stops after skipping ahead; reopening the player clears it.')
            page.get_by_role('button', name='Save report', exact=True).click()
            expect(page.get_by_role('status')).to_have_text('Report saved. It’s out of your head.')
            page.reload()
            page.get_by_role('button', name='[Demo] Jellyfin playback stalls after seeking', exact=True).click()
            dialog = page.get_by_role('dialog')
            expect(dialog).to_be_visible()
            dialog.get_by_label('Status').select_option('resolved')
            dialog.get_by_role('button', name='Save changes').click()
            expect(dialog).not_to_be_visible()
            page.get_by_role('button', name='Resolved', exact=False).click()
            page.get_by_role('button', name='[Demo] Jellyfin playback stalls after seeking', exact=True).click()
            dialog.get_by_label('Status').select_option('open')
            dialog.get_by_label('Report title').fill('[Demo] Jellyfin stalls when skipping ahead')
            dialog.get_by_role('button', name='Save changes').click()
            expect(dialog).not_to_be_visible()
            page.get_by_role('button', name='Inbox', exact=False).click()
            expect(page.get_by_role('button', name='[Demo] Jellyfin stalls when skipping ahead', exact=True)).to_be_visible()
            for name, service, kind in [('[Demo] Relay window forgets its size', 'Relay', 'bug'), ('[Demo] Add a compact service view', 'Homelab', 'idea')]:
                title.fill(name)
                page.locator('#capture-form').get_by_label('Service / project').fill(service)
                page.locator('#capture-form').get_by_label('Kind', exact=True).select_option(kind)
                page.get_by_role('button', name='Save report', exact=True).click()
                expect(page.get_by_role('status')).to_have_text('Report saved. It’s out of your head.')
            page.get_by_role('button', name='[Demo] Relay window forgets its size', exact=True).click()
            dialog.get_by_label('Status').select_option('in_progress')
            dialog.get_by_role('button', name='Save changes').click()
            expect(dialog).not_to_be_visible()
            search = page.get_by_label('Search reports')
            search.fill('Jellyfin')
            expect(page.locator('.report-row')).to_have_count(1)
            search.fill('nothing-matches-this')
            expect(page.get_by_text('No matching reports.')).to_be_visible()
            search.fill('')
            page.get_by_label('Filter by service').select_option('Relay')
            expect(page.locator('.report-row')).to_have_count(1)
            page.get_by_label('Filter by service').select_option('')
            with page.expect_download() as download:
                page.get_by_role('link', name='Export reports').click()
            assert download.value.suggested_filename == 'exterminator-reports.json'
            expect(page.locator('.report-row')).to_have_count(3)
            page.screenshot(path=str(artifacts / 'prototype-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.get_by_role('link', name='Export reports')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.get_by_role('button', name='New report', exact=True).click()
            expect(title).to_be_focused()
            title.fill('[Demo] Mobile capture works')
            page.get_by_role('button', name='Save report', exact=True).click()
            expect(page.get_by_role('status')).to_have_text('Report saved. It’s out of your head.')
            page.screenshot(path=str(artifacts / 'prototype-mobile.png'), full_page=True)
            assert page.locator('.report-title').first.bounding_box()['height'] >= 44
            # A network outage must preserve the draft and never announce a save.
            page.route('**/api/reports', lambda route: route.abort())
            title.fill('Unsaved outage draft')
            page.get_by_role('button', name='Save report', exact=True).click()
            expect(page.locator('#capture-error')).to_contain_text('Could not reach the local server')
            expect(title).to_have_value('Unsaved outage draft')
            page.unroute('**/api/reports')
            # Stored markup must remain literal text, including after reload.
            hostile = '<img src=x onerror="window.xss=true">'
            title.fill(hostile)
            page.get_by_role('button', name='Save report', exact=True).click()
            expect(title).to_have_value('')
            page.reload()
            expect(page.get_by_role('button', name=hostile, exact=True)).to_be_visible()
            assert page.evaluate('window.xss') is None
            # Load failures have an explicit recovery path.
            page.route('**/api/reports', lambda route: route.abort())
            page.reload()
            expect(page.get_by_role('button', name='Retry loading reports')).to_be_visible()
            page.unroute('**/api/reports')
            page.get_by_role('button', name='Retry loading reports').click()
            expect(page.locator('.report-row')).to_have_count(5)
            for width in (320, 768, 1024, 1440):
                page.set_viewport_size({'width': width, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), page.evaluate('[...document.querySelectorAll("body *")].filter(n=>n.getBoundingClientRect().right>innerWidth).map(n=>[n.tagName,n.className,n.getBoundingClientRect().right])')
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        thread.join()
