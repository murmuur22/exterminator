import importlib.util
import pytest


@pytest.mark.parametrize('payload', [{}, {'title': '  '}, {'title': 42}, {'title': 'x' * 181}, {'title': 'ok', 'kind': 'other'}, {'title': 'ok', 'status': 'deleted'}, {'title': 'ok', 'detail': None}, [], {'title': 'ok', 'unknown': True}])
def test_invalid_reports_are_rejected(tmp_path, payload):
    http = client(tmp_path)
    response = http.post('/api/reports', json=payload, headers={'X-Exterminator': '1'})
    assert response.status_code == 400
    assert http.get('/api/reports').json == []


def test_local_boundary_and_cross_site_requests(tmp_path):
    http = client(tmp_path)
    assert http.get('/api/reports', headers={'Host': 'evil.example'}).status_code == 400
    assert http.get('/api/reports', environ_overrides={'REMOTE_ADDR': '192.0.2.1'}).status_code == 403
    assert http.post('/api/reports', json={'title': 'bad'}).status_code == 403
    assert http.post('/api/reports', json={'title': 'bad'}, headers={'X-Exterminator': '1', 'Origin': 'https://evil.example'}).status_code == 403
    assert http.get('/api/reports', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert http.get('/submit').status_code == 404


def test_patch_validation_missing_id_and_security_headers(tmp_path):
    http = client(tmp_path)
    headers = {'X-Exterminator': '1'}
    report = http.post('/api/reports', json={'title': '  Valid  '}, headers=headers).json
    assert report['title'] == 'Valid'
    assert http.patch('/api/reports/1', json={'status': 'bad'}, headers=headers).status_code == 400
    assert http.patch('/api/reports/1', json={}, headers=headers).status_code == 400
    assert http.patch('/api/reports/999', json={'status': 'open'}, headers=headers).status_code == 404
    assert http.get('/api/reports').headers['Cache-Control'] == 'no-store'
    assert "default-src 'self'" in http.get('/api/reports').headers['Content-Security-Policy']


def client(tmp_path):
    assert importlib.util.find_spec('app'), 'Local HTTP app is not implemented'
    from app import create_app
    return create_app(tmp_path / 'reports.sqlite3').test_client()


def test_http_capture_edit_export(tmp_path):
    http = client(tmp_path)
    headers = {'Origin': 'http://localhost', 'X-Exterminator': '1'}
    response = http.post('/api/reports', json={'title': 'Jellyfin stalls', 'service': 'Jellyfin'}, headers=headers)
    assert response.status_code == 201
    report = response.json
    assert http.get('/api/reports').json == [report]
    response = http.patch(f"/api/reports/{report['id']}", json={'status': 'resolved'}, headers=headers)
    assert response.status_code == 200
    assert response.json['status'] == 'resolved'
    exported = http.get('/api/export')
    assert exported.json['reports'][0]['status'] == 'resolved'
    assert 'attachment' in exported.headers['Content-Disposition']
