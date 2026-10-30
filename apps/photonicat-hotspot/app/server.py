#!/usr/bin/python3
"""Unprivileged AP web app, independent of the NAS web server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import json
import ipaddress
import os
from pathlib import Path
import socketserver
import socket
import sys
import secrets
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parent))
from login import LoginMixin,LoginState

PREFIX = '/photonicat-hotspot'
ROOT = Path(__file__).resolve().parent
SESSIONS = {}
ATTEMPTS = {}
LOCK = threading.Lock()
BROKER = None
LOGIN = LoginState('pcat_hotspot','/photonicat-hotspot/')


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class WebServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,*args):
        self.workers=threading.BoundedSemaphore(32)
        super().__init__(*args)
    def process_request(self,request,address):
        if not self.workers.acquire(False):self.shutdown_request(request);return
        try:super().process_request(request,address)
        except Exception:self.workers.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.workers.release()


class Handler(LoginMixin,BaseHTTPRequestHandler):
    login_state = LOGIN
    def setup(self):
        super().setup();self.connection.settimeout(10)
    def log_message(self, *_args):
        pass

    def respond(self, status, data, mime='application/json', cookie=None, cookies=()):
        self.send_response(status)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(data)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        for value in cookies:self.send_header('Set-Cookie',value)
        self.end_headers()
        self.wfile.write(data)

    def peer(self):
        if isinstance(self.client_address,tuple):
            peer=self.client_address[0]
            if ipaddress.ip_address(peer).is_loopback and self.headers.get('X-Real-IP'):
                try:return str(ipaddress.ip_address(self.headers['X-Real-IP']))
                except ValueError:pass
            return peer
        return 'local'

    def controller(self, request):
        if not BROKER:raise OSError('Radio broker is not configured')
        with socket.socket(socket.AF_UNIX) as channel:
            channel.settimeout(120);channel.connect(str(BROKER))
            channel.sendall(json.dumps(request).encode()+b'\n')
            chunks=[];size=0
            while block:=channel.recv(65536):
                chunks.append(block);size+=len(block)
                if size>131072:raise ValueError('Response too large')
            return json.loads(b''.join(chunks))

    def auth_peer(self):return self.peer()

    def auth_secure(self):
        return isinstance(self.client_address,tuple) and ipaddress.ip_address(self.client_address[0]).is_loopback and self.headers.get('X-Forwarded-Proto')=='https'

    def auth_backend(self, request):return self.controller(request)

    def auth_reply(self,status,value,cookies):
        self.respond(status,json.dumps(value,ensure_ascii=False).encode(),cookies=cookies)

    def route(self, payload=None):
        if isinstance(self.client_address,tuple) and not ipaddress.ip_address(self.peer()).is_private:
            self.respond(403,b'{"ok":false}');return
        origin=self.headers.get('Origin')
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc!=self.headers.get('Host'):
                self.respond(403,b'{"ok":false}');return
        path = self.path.split('?', 1)[0].rstrip('/')
        if payload is None and path==PREFIX+'/logo.png':
            self.respond(200,(ROOT/'ui/images/settings-v2_256.png').read_bytes(),'image/png')
            return
        static = {PREFIX: ('index.html', 'text/html'), PREFIX + '/app.js': ('app.js', 'application/javascript'),
                  PREFIX + '/style.css': ('style.css', 'text/css'),
                  PREFIX+'/login.js':('login.js','application/javascript'),PREFIX+'/login.css':('login.css','text/css')}
        if payload is None and path in static:
            name, mime = static[path]
            self.respond(200, (ROOT / 'www' / name).read_bytes(), mime)
            return
        if payload is None and not path:
            self.send_response(302);self.send_header('Location',PREFIX+'/');self.end_headers();return
        if payload is not None and self.headers.get('X-Photonicat-Request') != '1':
            self.respond(403, b'{"ok":false}')
            return
        if payload is not None and path in {PREFIX+'/'+action for action in ('login','resume','forget','logout','login-state')} and isinstance(payload,dict):
            try:
                self.login_action(path.rsplit('/',1)[1],payload)
            except (ValueError, OSError):
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
        except (ValueError, OSError):
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
    if len(sys.argv)!=3:raise SystemExit('Broker path and service port are required')
    BROKER=Path(sys.argv[1])
    with WebServer(('0.0.0.0',int(sys.argv[2])),Handler) as server:
        server.daemon_threads=True
        server.serve_forever()
