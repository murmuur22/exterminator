"""Strict production configuration; no proxy-header trust or built-in identity."""
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re

CONFIG = Path('/etc/exterminator/config.json')
DATABASE = Path('/var/lib/exterminator/reports.sqlite3')
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10'))


def load_config(path=CONFIG):
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or set(raw) - {'admin', 'submission'}:
        raise ValueError('Unknown configuration keys')
    result = {}
    for name, port in (('admin', 8741), ('submission', 8742)):
        section = raw.get(name, {})
        if not isinstance(section, dict) or set(section) - {'bind', 'allowed_peers', 'allow_nonloopback', 'relay_origin'}:
            raise ValueError('Unknown surface settings')
        bind = section.get('bind', '127.0.0.1')
        address = ipaddress.IPv4Address(bind)
        opt_in = section.get('allow_nonloopback', False)
        if type(opt_in) is not bool:
            raise ValueError('allow_nonloopback must be boolean')
        if bind != '127.0.0.1' and (not opt_in or not any(address in net for net in PRIVATE_NETWORKS)):
            raise ValueError('Nonloopback binding requires an explicit private IPv4 opt-in')
        peers = section.get('allowed_peers', ['127.0.0.1'] if bind == '127.0.0.1' else [])
        if not isinstance(peers, list) or not peers or bind not in peers:
            raise ValueError('Exact allowed_peers must include the bind address for local health checks')
        for peer in peers:
            ip = ipaddress.IPv4Address(peer)
            if str(ip) != peer or (peer != '127.0.0.1' and (not opt_in or not any(ip in net for net in PRIVATE_NETWORKS))):
                raise ValueError('Peers must be exact loopback/private IPv4 addresses, not networks')
        origin = section.get('relay_origin', '')
        if not isinstance(origin, str) or (origin and not re.fullmatch(
            r'https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[1-9][0-9]{0,4})?', origin)):
            raise ValueError('relay_origin must be one exact HTTPS origin with no trailing slash')
        if origin and ':' in origin[8:] and int(origin.rsplit(':', 1)[1]) > 65535:
            raise ValueError('Invalid Relay port')
        result[name] = Surface(bind, port, tuple(peers), origin)
    return result


@dataclass(frozen=True)
class Surface:
    bind: str
    port: int
    allowed_peers: tuple[str, ...]
    relay_origin: str = ''

    @property
    def authority(self):
        return f'{self.bind}:{self.port}'


def gunicorn_options(surface):
    return dict(bind=surface.authority, workers=2, worker_class='sync',
                timeout=30, graceful_timeout=30, keepalive=2, backlog=64,
                max_requests=1000, max_requests_jitter=100,
                limit_request_line=2048, limit_request_fields=40,
                limit_request_field_size=4096, forwarded_allow_ips='',
                secure_scheme_headers={}, forwarder_headers='',
                accesslog=None, errorlog='-', loglevel='critical',
                capture_output=False, umask=0o077)


def serve(surface_name):
    from gunicorn.app.base import BaseApplication
    from app import create_app

    surface = load_config()[surface_name]

    class Server(BaseApplication):
        def load_config(self):
            for name, value in gunicorn_options(surface).items():
                self.cfg.set(name, value)

        def load(self):
            return create_app(DATABASE, surface=surface_name, boundary=surface)

    Server().run()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('surface', choices=['admin', 'submission'])
    serve(parser.parse_args().surface)
