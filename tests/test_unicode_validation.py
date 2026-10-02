import pytest
from app import create_app, create_submission_app


@pytest.mark.parametrize('bad', ['\ud800', '\udfff'])
def test_invalid_utf8_rejected_without_mutation_or_exception_log(tmp_path, caplog, bad):
    db = tmp_path / 'reports.sqlite3'
    admin, submit = create_app(db).test_client(), create_submission_app(db).test_client()
    headers = {'X-Exterminator': '1'}
    report = admin.post('/api/reports', json={'title': 'Valid 🐛'}, headers=headers).json
    assert report['title'] == 'Valid 🐛'
    for http, method, route, body in [(admin,'post','/api/reports',{'title':bad}),
                                    (submit,'post','/api/submit',{'title':'ok','detail':bad}),
                                    (admin,'patch','/api/reports/1',{'service':bad})]:
        response = getattr(http, method)(route, json=body, headers=headers)
        assert response.status_code == 400
    assert admin.get('/api/reports').json == [report]
    assert not [record for record in caplog.records if record.levelno >= 40]
