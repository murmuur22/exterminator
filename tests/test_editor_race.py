"""A pending save must not let a later editor draft be closed by its response."""
import threading
from werkzeug.serving import make_server
from flask import request
from playwright.sync_api import sync_playwright, expect
from app import create_app


def test_pending_edit_blocks_close_until_save_settles(tmp_path):
    app = create_app(tmp_path / 'reports.sqlite3')
    arrived, release = threading.Event(), threading.Event()

    @app.after_request
    def delay_patch(response):
        if request.method == 'PATCH':
            arrived.set()
            release.wait(15)
        return response

    app.test_client().post('/api/reports', json={'title': 'First report'}, headers={'X-Exterminator': '1'})
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.get_by_role('button', name='First report', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.get_by_label('Report title').fill('Edited report')
            dialog.get_by_role('button', name='Save changes').click()
            assert arrived.wait(3), 'PATCH did not arrive'
            expect(dialog.get_by_role('button', name='Close report')).to_be_disabled()
            expect(dialog.get_by_role('button', name='Cancel', exact=True)).to_be_disabled()
            page.keyboard.press('Escape')
            expect(dialog).to_be_visible()
            release.set()
            expect(dialog).not_to_be_visible()
            page.get_by_role('button', name='Edited report', exact=True).click()
            expect(dialog.get_by_role('button', name='Close report')).to_be_enabled()
            expect(dialog.get_by_role('button', name='Cancel', exact=True)).to_be_enabled()
            page.keyboard.press('Escape')
            expect(dialog).not_to_be_visible()
            browser.close()
    finally:
        release.set()
        server.shutdown()
        thread.join()
