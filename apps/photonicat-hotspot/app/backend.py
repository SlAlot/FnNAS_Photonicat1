#!/usr/bin/python3
"""Root-only radio broker on a private socket; no TCP or arbitrary command API."""
import grp
import fcntl
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import socketserver
import struct
import sys
import threading

from ap import Runtime
from remember import RememberAuth


class Broker(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self,address,handler,runtime,uid):
        self.runtime,self.uid = runtime,uid
        self.auth = RememberAuth(runtime.data/'auth-secret','photonicat-hotspot')
        self.workers = threading.BoundedSemaphore(8)
        super().__init__(address,handler)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not self.server.workers.acquire(False):return
        try:
            _,uid,_ = struct.unpack('3i',self.request.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid not in (0,self.server.uid):return
            self.request.settimeout(5)
            with self.request.makefile('rb') as stream:raw = stream.readline(8193)
            if len(raw)>8192 or not raw.endswith(b'\n'):raise ValueError('请求过大')
            value = json.loads(raw)
            if not isinstance(value,dict):raise ValueError('请求无效')
            operation = value.get('operation','status')
            if operation=='authenticate':result=self.server.auth.authenticate(value)
            elif operation=='health':result={'ok':True}
            elif operation in ('status','apply'):
                if operation=='apply':self.server.runtime.apply(value)
                result={'ok':True,'devices':self.server.runtime.status()}
            else:raise ValueError('不支持的操作')
        except Exception as error:result={'ok':False,'error':str(error)[:500]}
        finally:self.server.workers.release()
        try:
            self.request.settimeout(5)
            self.request.sendall(json.dumps(result,ensure_ascii=False).encode()+b'\n')
        except OSError:pass


def main():
    if os.geteuid()!=0 or len(sys.argv) not in (5,6):raise SystemExit(1)
    cleanup = len(sys.argv)==6 and sys.argv[5]=='--cleanup'
    if len(sys.argv)==6 and not cleanup:raise SystemExit(1)
    app,data,user,group = Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3],sys.argv[4]
    lock = open('/run/photonicat-hotspot-apply.lock','w')
    try:fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Another hotspot controller is running')
    runtime = Runtime(app,data)
    if cleanup:
        runtime.shutdown()
        (data/'runtime/backend.sock').unlink(missing_ok=True)
        return
    path = data/'runtime/backend.sock';path.unlink(missing_ok=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    with Broker(str(path),Handler,runtime,pwd.getpwnam(user).pw_uid) as server:
        os.chown(path,0,grp.getgrnam(group).gr_gid);path.chmod(0o660)
        server.timeout=.5
        startup = threading.Thread(target=runtime.start_saved,daemon=True);startup.start()
        try:
            while not stop.is_set():server.handle_request()
        finally:
            runtime.shutdown()
            path.unlink(missing_ok=True)


if __name__=='__main__':main()
