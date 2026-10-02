import sqlite3

import pytest

from app import VERSION, create_app
from store import Store


def test_health_checks_database_without_disclosing_reports(tmp_path):
    path = tmp_path / 'reports.sqlite3'
    http = create_app(path, surface='submission').test_client()
    Store(path).create({'title': 'PRIVATE'})
    response = http.get('/healthz')
    assert response.status_code == 200
    assert response.json == {'ok': True, 'surface': 'submission', 'version': VERSION}
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE reports')
    assert http.get('/healthz').status_code == 503
    assert http.get('/healthz').json == {'error': 'Database unavailable.'}


def test_production_boundary_is_exact_and_forwarded_headers_are_ignored(tmp_path):
    import importlib.util
    assert importlib.util.find_spec('production'), 'Production configuration is missing'
    from production import Surface
    boundary = Surface('192.168.50.10', 8741, ('192.168.50.10',), 'https://relay.example')
    http = create_app(tmp_path / 'db', boundary=boundary).test_client()
    def get(**kw):
        return http.get('/healthz', base_url='http://192.168.50.10:8741',
                        environ_overrides={'REMOTE_ADDR': '192.168.50.10'}, **kw)
    assert get().status_code == 200
    assert 'frame-ancestors https://relay.example;' in get().headers['Content-Security-Policy']
    for host in ('192.168.50.10', '192.168.50.10:8742', 'relay.example', 'localhost:8741'):
        assert get(headers={'Host': host}).status_code == 400
    assert http.get('/healthz', base_url='http://192.168.50.10:8741',
                    environ_overrides={'REMOTE_ADDR': '192.168.50.11'},
                    headers={'X-Forwarded-For': '192.168.50.10'}).status_code == 403
    assert get(headers={'Origin': 'https://relay.example'}).status_code == 403
    headers = {'X-Exterminator': '1', 'X-Forwarded-Host': 'evil.example', 'X-Forwarded-Proto': 'https'}
    assert http.post('/api/reports', base_url='http://192.168.50.10:8741',
                     environ_overrides={'REMOTE_ADDR': '192.168.50.10'},
                     headers=headers, json={'title': 'gateway, no Origin'}).status_code == 201
    headers['Origin'] = 'https://192.168.50.10:8741'
    assert http.post('/api/reports', base_url='http://192.168.50.10:8741',
                     environ_overrides={'REMOTE_ADDR': '192.168.50.10'},
                     headers=headers, json={'title': 'bad origin'}).status_code == 403
    headers['Origin'] = 'http://192.168.50.10:8741'
    assert http.post('/api/reports', base_url='http://192.168.50.10:8741',
                     environ_overrides={'REMOTE_ADDR': '192.168.50.10'},
                     headers=headers, json={'title': 'direct'}).status_code == 201


def test_config_defaults_and_independent_opt_in(tmp_path):
    import json
    import production
    assert hasattr(production, 'load_config'), 'Config loader missing'
    config = tmp_path / 'config.json'
    config.write_text('{}')
    settings = production.load_config(config)
    assert settings['admin'].authority == '127.0.0.1:8741'
    assert settings['submission'].authority == '127.0.0.1:8742'
    config.write_text(json.dumps({'admin': {'bind': '192.168.50.10', 'allow_nonloopback': True,
        'allowed_peers': ['192.168.50.10'], 'relay_origin': 'https://relay.example:8443'}}))
    settings = production.load_config(config)
    assert settings['admin'].allowed_peers == ('192.168.50.10',)
    assert settings['submission'].bind == '127.0.0.1'


@pytest.mark.parametrize('section', [
    {'bind': '0.0.0.0'}, {'bind': '192.168.50.10'},
    {'bind': '8.8.8.8', 'allow_nonloopback': True, 'allowed_peers': ['8.8.8.8']},
    {'bind': '192.168.50.10', 'allow_nonloopback': True},
    {'allowed_peers': ['127.0.0.0/8']}, {'allowed_peers': ['*']},
    {'allowed_peers': []}, {'allowed_peers': ['192.168.50.10']},
    {'allow_nonloopback': 'true'}, {'port': 80}, {'unknown': True},
    *[{'relay_origin': origin} for origin in ('http://relay.example', 'https://*.example',
      'https://relay.example/', 'https://relay.example/path', 'https://user@relay.example',
      'https://relay.example; img-src *', 'https://relay.example?x', 'https://relay.example#x')],
])
def test_invalid_production_config_refused(tmp_path, section):
    import json
    import production
    assert hasattr(production, 'load_config'), 'Config loader missing'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'admin': section}))
    with pytest.raises(ValueError):
        production.load_config(config)


def test_runtime_is_bounded_and_ignores_proxy_headers(tmp_path):
    import production
    assert hasattr(production, 'gunicorn_options'), 'Gunicorn runtime missing'
    options = production.gunicorn_options(production.Surface('127.0.0.1', 8742, ('127.0.0.1',)))
    assert options['bind'] == '127.0.0.1:8742'
    assert options['workers'] == 2
    assert options['worker_class'] == 'sync'
    assert options['timeout'] == 30
    assert options['forwarded_allow_ips'] == ''
    assert options['secure_scheme_headers'] == {}
    assert options['accesslog'] is None
    assert options['limit_request_line'] <= 4094
    assert options['limit_request_fields'] <= 50


@pytest.mark.parametrize('surface,route', [('admin', '/api/reports'), ('submission', '/api/submit')])
def test_request_size_and_malformed_json_limits(tmp_path, surface, route):
    http = create_app(tmp_path / 'db', surface=surface).test_client()
    headers = {'X-Exterminator': '1'}
    assert http.post(route, data='x' * 32769, content_type='application/json', headers=headers).status_code == 413
    assert http.post(route, data='{', content_type='application/json', headers=headers).status_code == 400
    assert http.post(route, data='{}', content_type='text/plain', headers=headers).status_code == 415


def test_oversized_report_id_and_deep_json_are_client_errors(tmp_path, caplog):
    http = create_app(tmp_path / 'db').test_client()
    response = http.patch('/api/reports/99999999999999999999999999999', json={'title': 'private'}, headers={'X-Exterminator': '1'})
    assert response.status_code == 404
    response = http.post('/api/reports', data='[' * 2000 + ']' * 2000, content_type='application/json', headers={'X-Exterminator': '1'})
    assert response.status_code == 400
    assert not caplog.records


def test_health_refuses_new_schema_and_legacy_init_validates_layout(tmp_path):
    path = tmp_path / 'db'
    http = create_app(path).test_client()
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=2')
    assert http.get('/healthz').status_code == 503
    other = tmp_path / 'other'
    with sqlite3.connect(other) as db:
        db.execute('CREATE TABLE reports (id TEXT, title TEXT, service TEXT, kind TEXT, detail TEXT, status TEXT, created_at TEXT, updated_at TEXT)')
    with pytest.raises(sqlite3.DatabaseError, match='Unsupported schema'):
        Store(other)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=0')
    assert Store(path).list() == []
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1


def test_schema_version_init_and_newer_refusal(tmp_path):
    path = tmp_path / 'reports.sqlite3'
    Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        db.execute('PRAGMA user_version=2')
    with pytest.raises(sqlite3.DatabaseError, match='Unsupported schema'):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2


def test_unknown_existing_schema_is_not_adopted(tmp_path):
    path = tmp_path / 'reports.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE unrelated (value TEXT)')
    with pytest.raises(sqlite3.DatabaseError, match='Unsupported schema'):
        Store(path)


def test_database_errors_are_generic_and_not_logged(tmp_path, caplog):
    path = tmp_path / 'reports.sqlite3'
    http = create_app(path).test_client()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE reports')
    response = http.post('/api/reports', json={'title': 'PRIVATE'}, headers={'X-Exterminator': '1'})
    assert response.status_code == 503
    assert response.json == {'error': 'Database unavailable.'}
    assert not caplog.records
