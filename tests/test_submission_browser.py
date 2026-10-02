import threading
from pathlib import Path
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect
from app import create_app, create_submission_app


def test_submission_form_to_admin_inbox(tmp_path):
    db = tmp_path / 'shared.sqlite3'
    apps = [create_app(db), create_submission_app(db)]
    servers = [make_server('127.0.0.1', 0, app, threaded=True) for app in apps]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width': 1100, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            url = f'http://127.0.0.1:{servers[1].server_port}'
            assert page.goto(url).status == 200, 'Submission interface is not implemented'
            expect(page.get_by_role('heading', name='Eek! A bug!')).to_be_visible()
            assert page.get_by_role('link', name='Export reports').count() == 0
            assert page.locator('#reports, #views, #editor').count() == 0
            page.screenshot(path=str(artifacts / 'submission-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(artifacts / 'submission-mobile.png'), full_page=True)
            title = page.get_by_label('What happened?')
            title.fill('[Demo] The player froze')
            page.get_by_label('Service / project').fill('Jellyfin')
            page.get_by_label('Details').fill('Demo submission only. No actual service issue has been verified.')
            # Losing the connection retains input and does not show success.
            page.route('**/api/submit', lambda route: route.abort())
            page.get_by_role('button', name='Send report', exact=True).click()
            expect(page.get_by_role('alert')).to_contain_text('Could not confirm')
            expect(title).to_have_value('[Demo] The player froze')
            expect(page.get_by_role('heading', name='Report received!')).not_to_be_visible()
            page.unroute('**/api/submit')
            with page.expect_response('**/api/submit') as response:
                page.get_by_role('button', name='Send report', exact=True).click()
            assert response.value.status == 201
            assert response.value.json() == {'ok': True}
            expect(page.get_by_role('heading', name='Report received!')).to_be_visible()
            expect(page.get_by_role('button', name='Send report', exact=True)).not_to_be_visible()
            page.screenshot(path=str(artifacts / 'submission-success.png'), full_page=True)
            admin = browser.new_page()
            admin.goto(f'http://127.0.0.1:{servers[0].server_port}')
            admin.get_by_role('button', name='[Demo] The player froze', exact=True).click()
            expect(admin.get_by_role('dialog').get_by_label('Details')).to_have_value('Demo submission only. No actual service issue has been verified.')
            expect(admin.get_by_role('dialog').get_by_label('Status')).to_have_value('open')
            page.get_by_role('button', name='Send another report').click()
            expect(title).to_have_value('')
            expect(title).to_be_focused()
            for width in (320, 390, 768, 1100):
                page.set_viewport_size({'width': width, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            for route in ('/api/reports', '/api/export', '/api/reports/1'):
                assert page.request.get(url + route).status == 404
            assert not errors, errors
            browser.close()
    finally:
        for server in servers:
            server.shutdown()
        for thread in threads:
            thread.join()
