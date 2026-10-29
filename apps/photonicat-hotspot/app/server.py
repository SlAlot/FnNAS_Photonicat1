#!/usr/bin/python3
"""Unix-socket app authenticated against existing NAS administrator accounts."""
from http.server import BaseHTTPRequestHandler
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import socketserver
import subprocess
import secrets
import time

PREFIX = '/photonicat-hotspot'
ROOT = Path(__file__).resolve().parent
SESSIONS = {}
ATTEMPTS = {}


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, data, mime='application/json', cookie=None):
        self.send_response(status)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(data)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(data)

    def admin(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
            token = cookies['pcat_hotspot'].value
        except (KeyError, ValueError):
            return False
        return SESSIONS.get(token, 0) > time.monotonic()

    def controller(self, request):
        result = subprocess.run(['sudo', '-n', '/usr/local/sbin/photonicat-hotspotctl'],
            input=json.dumps(request), capture_output=True, text=True, timeout=60)
        return json.loads(result.stdout)

    def login(self, payload):
        peer = self.headers.get('X-Real-IP', 'local')
        now = time.monotonic()
        attempts = [stamp for stamp in ATTEMPTS.get(peer, []) if now - stamp < 60]
        ATTEMPTS[peer] = attempts
        if len(attempts) >= 5:
            self.respond(429, '{"ok":false,"error":"尝试次数过多，请一分钟后重试"}'.encode())
            return
        attempts.append(now)
        response = self.controller({'operation': 'authenticate', 'username': payload.get('username'),
                                    'password': payload.get('password')})
        if not response.get('ok'):
            self.respond(401, json.dumps(response, ensure_ascii=False).encode())
            return
        for token, expiry in list(SESSIONS.items()):
            if expiry <= now:
                SESSIONS.pop(token, None)
        if len(SESSIONS) > 100:
            self.respond(429, b'{"ok":false}')
            return
        token = secrets.token_urlsafe(32)
        SESSIONS[token] = now + 3600
        ATTEMPTS[peer] = []
        secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
        self.respond(200, b'{"ok":true}', cookie='pcat_hotspot=' + token +
                     '; Path=/photonicat-hotspot/; HttpOnly; SameSite=Strict; Max-Age=3600' + secure)

    def route(self, payload=None):
        path = self.path.split('?', 1)[0].rstrip('/')
        static = {PREFIX: ('index.html', 'text/html'), PREFIX + '/app.js': ('app.js', 'application/javascript'),
                  PREFIX + '/style.css': ('style.css', 'text/css')}
        if payload is None and path in static:
            name, mime = static[path]
            self.respond(200, (ROOT / 'www' / name).read_bytes(), mime)
            return
        if payload is not None and self.headers.get('X-Photonicat-Request') != '1':
            self.respond(403, b'{"ok":false}')
            return
        if path == PREFIX + '/login' and payload is not None and isinstance(payload, dict):
            try:
                self.login(payload)
            except (ValueError, OSError, subprocess.SubprocessError):
                self.respond(503, b'{"ok":false,"error":"Login service unavailable"}')
            return
        if not self.admin():
            self.respond(401, b'{"ok":false,"error":"Administrator login required"}')
            return
        if path != PREFIX + '/api' or (payload is not None and self.headers.get('X-Photonicat-Request') != '1'):
            self.respond(404, b'{"ok":false}')
            return
        request = {'operation': 'status'} if payload is None else payload
        if not isinstance(request, dict) or (payload is not None and request.get('operation') != 'apply'):
            self.respond(400, b'{"ok":false}')
            return
        try:
            response = self.controller(request)
            self.respond(200 if response.get('ok') else 400, json.dumps(response, ensure_ascii=False).encode())
        except (ValueError, OSError, subprocess.SubprocessError):
            self.respond(503, b'{"ok":false,"error":"Hotspot service unavailable"}')

    def do_GET(self):
        self.route()

    def do_POST(self):
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            self.respond(415, b'{"ok":false}')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError()
            self.route(json.loads(self.rfile.read(length)))
        except ValueError:
            self.respond(400, b'{"ok":false}')


if __name__ == '__main__':
    socket = Path('/run/photonicat-hotspot/app.sock')
    socket.unlink(missing_ok=True)
    os.umask(0o117)
    with Server(str(socket), Handler) as server:
        server.serve_forever()
