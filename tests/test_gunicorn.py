"""Real Gunicorn smoke test on owned ephemeral sockets, never prototype ports."""
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from app import VERSION


def test_real_gunicorn_two_surfaces(tmp_path):
    processes, sockets = [], []
    log = (tmp_path / 'runtime.log').open('w+')
    try:
        for surface in ('admin', 'submission'):
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            sock.listen(64)
            sockets.append(sock)
            port = sock.getsockname()[1]
            code = (
                'from pathlib import Path; import production; '
                f'production.DATABASE=Path({str(tmp_path / "db")!r}); '
                f'production.load_config=lambda: {{{surface!r}: production.Surface("127.0.0.1", {port}, ("127.0.0.1",))}}; '
                'original=production.gunicorn_options; '
                f'production.gunicorn_options=lambda s: dict(original(s), bind="fd://{sock.fileno()}"); '
                f'production.serve({surface!r})'
            )
            processes.append(subprocess.Popen([sys.executable, '-B', '-c', code],
                cwd=Path(__file__).resolve().parents[1], pass_fds=(sock.fileno(),),
                stdout=log, stderr=log, start_new_session=True))

        def request(index, method, path, data=None, headers=None):
            conn = http.client.HTTPConnection('127.0.0.1', sockets[index].getsockname()[1], timeout=2)
            try:
                conn.request(method, path, body=json.dumps(data) if data is not None else None,
                             headers=headers or {})
                response = conn.getresponse()
                return response.status, response.read()
            finally:
                conn.close()

        for index, surface in enumerate(('admin', 'submission')):
            deadline = time.monotonic() + 10
            while True:
                assert processes[index].poll() is None, (tmp_path / 'runtime.log').read_text()
                try:
                    status, body = request(index, 'GET', '/healthz')
                    if status == 200:
                        assert json.loads(body) == {'ok': True, 'surface': surface, 'version': VERSION}
                        break
                except (OSError, http.client.HTTPException):
                    pass
                assert time.monotonic() < deadline, (tmp_path / 'runtime.log').read_text()
                time.sleep(0.05)
        headers = {'Content-Type': 'application/json', 'X-Exterminator': '1',
                   'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '8.8.8.8',
                   'Origin': 'http://127.0.0.1:' + str(sockets[1].getsockname()[1])}
        assert request(1, 'POST', '/api/submit', {'title': 'private runtime test'}, headers) == (201, b'{"ok":true}\n')
        status, body = request(0, 'GET', '/api/reports')
        assert status == 200
        assert json.loads(body)[0]['title'] == 'private runtime test'
        assert request(1, 'GET', '/api/reports')[0] == 404
        assert request(0, 'GET', '/healthz', headers={'Host': '127.0.0.1:1'})[0] == 400
        assert request(0, 'GET', '/healthz', headers={'Origin': 'https://relay.example'})[0] == 403
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for sock in sockets:
            sock.close()
        log.close()
    assert 'private runtime test' not in (tmp_path / 'runtime.log').read_text()
