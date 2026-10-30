#!/usr/bin/python3
"""Real DHCP/DNS/NAT check on a plugin bridge using an isolated test client."""
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import urllib.request


def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True)


def client(interface):
    mac=bytes.fromhex(Path('/sys/class/net/'+interface+'/address').read_text().strip().replace(':',''))
    xid=secrets.randbits(32)
    header=struct.pack('!BBBBIHH4s4s4s4s16s64s128s',1,1,6,0,xid,0,0x8000,b'\0'*4,b'\0'*4,b'\0'*4,b'\0'*4,mac+b'\0'*10,b'\0'*64,b'\0'*128)
    cookie=b'\x63\x82\x53\x63'
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,interface.encode()+b'\0')
        sock.bind(('',68));sock.settimeout(10)
        def exchange(options,kind):
            sock.sendto(header+cookie+options+b'\x37\x03\x01\x03\x06\xff',('255.255.255.255',67))
            while True:
                data,_=sock.recvfrom(4096)
                if len(data)<240 or struct.unpack('!I',data[4:8])[0]!=xid:continue
                fields={};offset=240
                while offset<len(data):
                    code=data[offset];offset+=1
                    if code==255:break
                    if code==0:continue
                    size=data[offset];offset+=1;fields[code]=data[offset:offset+size];offset+=size
                if fields.get(53)==bytes([kind]):return data,fields
        offer,opts=exchange(b'\x35\x01\x01',2)
        address=offer[16:20];server=opts[54]
        ack,opts=exchange(b'\x35\x01\x03\x32\x04'+address+b'\x36\x04'+server,5)
    ip=socket.inet_ntoa(address);gateway=socket.inet_ntoa(opts[3][:4]);dns=socket.inet_ntoa(opts[6][:4])
    run('ip','addr','add',ip+'/24','dev',interface)
    run('ip','route','add','default','via',gateway)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
        udp.settimeout(8)
        query=struct.pack('!HHHHHH',32123,0x100,1,0,0,0)+b'\x07example\x03com\x00\x00\x01\x00\x01'
        udp.sendto(query,(dns,53));response,_=udp.recvfrom(4096)
        assert response[:2]==query[:2] and struct.unpack('!H',response[6:8])[0]>0,'DNS did not return an answer'
    with urllib.request.urlopen('http://connectivitycheck.platform.hicloud.com/generate_204',timeout=12) as http:
        assert http.status==204,http.status
    print(json.dumps({'dhcp':True,'dns':True,'internet':True,'client':ip,'gateway':gateway}))


def main():
    if sys.argv[1:2]==['client']:client(sys.argv[2]);return
    if os.geteuid()!=0 or sys.argv[1:] not in (['board'],['external']):raise SystemExit(1)
    bridge='pcat-ap-'+('b' if sys.argv[1]=='board' else 'e')
    suffix=secrets.token_hex(3);namespace='pcat-ap-check-'+suffix
    host='pav'+suffix;peer='pac'+suffix
    run('ip','link','show','dev',bridge)
    run('ip','netns','add',namespace)
    try:
        run('ip','link','add',host,'type','veth','peer','name',peer)
        run('ip','link','set',host,'master',bridge);run('ip','link','set',host,'up')
        run('ip','link','set',peer,'netns',namespace)
        run('ip','netns','exec',namespace,'ip','link','set','lo','up')
        run('ip','netns','exec',namespace,'ip','link','set',peer,'up')
        print(run('ip','netns','exec',namespace,'/usr/bin/python3',str(Path(__file__).resolve()),'client',peer).stdout.strip())
    finally:
        subprocess.run(['ip','link','delete',host],capture_output=True)
        subprocess.run(['ip','netns','delete',namespace],capture_output=True)


if __name__=='__main__':main()
