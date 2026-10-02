import app


def test_submission_is_write_only_and_shared_with_admin(tmp_path):
    assert hasattr(app, 'create_submission_app'), 'Separate submission app is not implemented'
    path = tmp_path / 'reports.sqlite3'
    admin = app.create_app(path).test_client()
    submit = app.create_submission_app(path).test_client()
    headers = {'X-Exterminator': '1'}
    admin.post('/api/reports', json={'title': 'PRIVATE ADMIN REPORT'}, headers=headers)
    response = submit.post('/api/submit', json={'title': 'Player freezes', 'service': 'Jellyfin', 'kind': 'bug'}, headers=headers)
    assert response.status_code == 201
    assert response.json == {'ok': True}
    reports = admin.get('/api/reports').json
    assert len(reports) == 2
    assert reports[0]['title'] == 'Player freezes'
    assert reports[0]['status'] == 'open'
    for method, route in [('get', '/api/reports'), ('head', '/api/reports'), ('get', '/api/export'), ('get', '/api/reports/1'), ('patch', '/api/reports/1'), ('post', '/api/reports'), ('get', '/admin'), ('get', '/static/app.js'), ('get', '/data/reports.sqlite3')]:
        blocked = getattr(submit, method)(route, headers=headers)
        assert blocked.status_code == 404, (method, route)
        assert b'PRIVATE ADMIN REPORT' not in blocked.data
    assert submit.get('/api/submit').status_code == 405
    assert admin.post('/api/submit', json={'title': 'No'}, headers=headers).status_code == 404
    assert len(admin.get('/api/reports').json) == 2


def test_submission_validation_and_origin_boundary(tmp_path):
    assert hasattr(app, 'create_submission_app'), 'Separate submission app is not implemented'
    http = app.create_submission_app(tmp_path / 'reports.sqlite3').test_client()
    for payload in ({}, {'title': ' '}, {'title': 'x', 'status': 'resolved'}, {'title': 'x', 'kind': 'other'}, {'title': 'x', 'detail': None}, {'title': 'x', 'id': 1}):
        assert http.post('/api/submit', json=payload, headers={'X-Exterminator': '1'}).status_code == 400
    assert http.post('/api/submit', json={'title': 'x'}).status_code == 403
    assert http.post('/api/submit', json={'title': 'x'}, headers={'X-Exterminator': '1', 'Origin': 'http://localhost:8741'}, base_url='http://localhost:8742').status_code == 403
    assert http.get('/', headers={'Host': 'evil.example'}).status_code == 400
    assert http.get('/', environ_overrides={'REMOTE_ADDR': '192.0.2.1'}).status_code == 403
