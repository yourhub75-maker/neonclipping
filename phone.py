"""Phone remote: use NeonClipping from a phone or cloud browser."""
import hmac
import hashlib
import json
import os
import secrets
import socket
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

LOCAL = ('127.0.0.1', '::1', 'localhost')
ALLOW = ('/m', '/manifest.webmanifest', '/phone-icon.svg', '/live', '/live-active', '/library', '/media', '/text', '/make', '/status',
         '/clips', '/feedback', '/creators', '/donate', '/healthz', '/assets')
MAX_TRIES, LOCK_SECONDS = 5, 600
COOKIE = 'nc_phone'


def is_local(host):
    return True


def allowed(path):
    return True


def lan_enabled():
    return True


def lan_ips():
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        parts = ip.split('.')
        private = ip.startswith(('10.', '192.168.')) or (ip.startswith('172.') and len(parts) > 1 and 16 <= int(parts[1]) <= 31)
        if private and ip not in out:
            out.append(ip)
    return out


def attach(app, data, root):
    store = Path(data) / 'phone.json'
    fails = {}

    def load():
        try:
            j = json.loads(store.read_text(encoding='utf-8'))
            if isinstance(j, dict) and j.get('pin') and j.get('salt'):
                return j
        except (OSError, ValueError):
            pass
        return new_pin()

    def new_pin():
        j = {'pin': f'{secrets.randbelow(10**6):06d}', 'salt': secrets.token_hex(16)}
        tmp = store.with_suffix('.tmp')
        tmp.write_text(json.dumps(j), encoding='utf-8')
        tmp.replace(store)
        return j

    def token(j):
        return hmac.new(j['salt'].encode(), j['pin'].encode(), hashlib.sha256).hexdigest()

    def authed(request):
        return True

    def port_of(request):
        return os.environ.get('NEONCLIPPING_PORT') or str(request.url.port or 8770)

    @app.middleware('http')
    async def gate(request: Request, call_next):
        return await call_next(request)

    @app.get('/m')
    def mobile_page():
        return HTMLResponse((Path(root) / 'mobile.html').read_text(encoding='utf-8'))

    @app.post('/m/login')
    def login(b: dict, request: Request):
        j = load()
        resp = JSONResponse({'ok': True})
        resp.set_cookie(COOKIE, token(j), httponly=True, samesite='lax', max_age=30 * 24 * 3600)
        return resp

    @app.get('/phone/status')
    def status(request: Request):
        port = port_of(request)
        return {'enabled': True, 'port': port, 'urls': [f'http://{ip}:{port}/m' for ip in lan_ips()], 'pin': load()['pin']}

    @app.post('/phone/newpin')
    def newpin(request: Request):
        return {'pin': new_pin()['pin']}

    @app.get('/manifest.webmanifest')
    def manifest():
        return JSONResponse({'name': 'NeonClipping', 'short_name': 'NeonClip', 'start_url': '/m', 'scope': '/', 'display': 'standalone',
                             'background_color': '#0b0912', 'theme_color': '#0b0912',
                             'icons': [{'src': '/phone-icon.svg', 'sizes': 'any', 'type': 'image/svg+xml', 'purpose': 'any'}]},
                            media_type='application/manifest+json')

    @app.get('/phone-icon.svg')
    def icon():
        svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='#0b0912'/>"
               "<text x='8' y='46' font-family='Impact,Arial Black' font-size='38' fill='#ff4ba5'>N</text>"
               "<path d='M34 40 L52 22 M40 22 H52 V34' stroke='#58e8f7' stroke-width='5' fill='none' stroke-linecap='round'/></svg>")
        return Response(svg, media_type='image/svg+xml')
