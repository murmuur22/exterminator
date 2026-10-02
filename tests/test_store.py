import importlib.util


def test_capture_persists_across_store_instances(tmp_path):
    assert importlib.util.find_spec('store'), 'Persistent report store is not implemented'
    from store import Store
    path = tmp_path / 'reports.sqlite3'
    report = Store(path).create({'title': 'Jellyfin playback stalls', 'service': 'Jellyfin', 'kind': 'bug', 'detail': 'After seeking.'})
    assert report['id'] == 1
    assert report['status'] == 'open'
    assert Store(path).list() == [report]


def test_edit_resolve_reopen_retains_report(tmp_path):
    from store import Store
    store = Store(tmp_path / 'reports.sqlite3')
    report = store.create({'title': 'Relay window issue'})
    assert hasattr(store, 'update'), 'Report editing is not implemented'
    updated = store.update(report['id'], {'title': 'Relay window resize issue', 'status': 'resolved'})
    assert updated['status'] == 'resolved'
    assert updated['title'] == 'Relay window resize issue'
    assert updated['created_at'] == report['created_at']
    assert store.update(report['id'], {'status': 'open'})['status'] == 'open'
    assert len(store.list()) == 1
    assert store.update(999, {'status': 'open'}) is None
