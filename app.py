"""Local-only prototype. No deployment or remote authentication support yet."""
from pathlib import Path
import tomllib

from flask import Flask, jsonify, request, render_template
from werkzeug.exceptions import HTTPException, BadRequest
from store import Store

ROOT = Path(__file__).resolve().parent
VERSION = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']


def create_app(database=None, *, surface='admin'):
    if surface not in ('admin', 'submission'):
        raise ValueError('Unknown application surface')
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=32_768, TRUSTED_HOSTS=['localhost', '127.0.0.1', '[::1]'])
    store = Store(database or ROOT / 'data' / 'reports.sqlite3')

    @app.before_request
    def local_boundary():
        if request.remote_addr not in ('127.0.0.1', '::1'):
            return jsonify(error='This prototype accepts local connections only.'), 403
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(error='Cross-site requests are not allowed.'), 403
        if request.method in ('POST', 'PATCH', 'DELETE', 'PUT'):
            origin = request.headers.get('Origin')
            if request.headers.get('X-Exterminator') != '1' or (origin and origin != request.host_url.rstrip('/')):
                return jsonify(error='Use the local Exterminator interface to make changes.'), 403

    @app.after_request
    def security_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    def validated_fields(creating=False):
        fields = request.get_json()
        limits = {'title': 180, 'service': 80, 'kind': 20, 'detail': 10000, 'status': 20}
        if not isinstance(fields, dict) or not fields or set(fields) - limits.keys():
            raise BadRequest('Provide report fields as a non-empty JSON object.')
        if creating and ('title' not in fields or 'status' in fields):
            raise BadRequest('New reports need a title and always start Open.')
        for key, value in fields.items():
            if not isinstance(value, str) or len(value) > limits[key]:
                raise BadRequest(f'{key.capitalize()} must be text of at most {limits[key]} characters.')
        fields = {key: value.strip() for key, value in fields.items()}
        if 'title' in fields and not fields['title']:
            raise BadRequest('Give this report a short title.')
        if 'kind' in fields and fields['kind'] not in ('bug', 'feedback', 'idea'):
            raise BadRequest('Choose Bug, Feedback, or Idea.')
        if 'status' in fields and fields['status'] not in ('open', 'in_progress', 'resolved'):
            raise BadRequest('Choose Open, In progress, or Resolved.')
        return fields

    @app.get('/')
    def index():
        template = 'submission.html' if surface == 'submission' else 'index.html'
        return render_template(template, version=VERSION)

    if surface == 'submission':
        @app.before_request
        def submission_assets_only():
            if request.endpoint == 'static' and request.view_args.get('filename') not in (
                'style.css', 'submission.css', 'submission.js', 'pixel-bug.svg', 'wallpaper.svg'
            ):
                return jsonify(error='Not found.'), 404

        @app.post('/api/submit')
        def submit():
            store.create(validated_fields(creating=True))
            # No report IDs, counts, stored content, or lookup capability returned.
            return jsonify(ok=True), 201

        return app  # Admin routes are never registered on this application.

    @app.get('/api/reports')
    def reports():
        return jsonify(store.list())

    @app.post('/api/reports')
    def capture():
        return jsonify(store.create(validated_fields(creating=True))), 201

    @app.patch('/api/reports/<int:report_id>')
    def update(report_id):
        result = store.update(report_id, validated_fields())
        return jsonify(result) if result else (jsonify(error='Report not found.'), 404)

    @app.get('/api/export')
    def export():
        response = jsonify(version=VERSION, reports=store.list())
        response.headers['Content-Disposition'] = 'attachment; filename="exterminator-reports.json"'
        return response

    return app


def create_submission_app(database=None):
    return create_app(database, surface='submission')


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=8741, debug=False)
