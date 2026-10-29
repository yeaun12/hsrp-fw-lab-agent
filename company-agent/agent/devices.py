"""장비 접속 계층: Cisco IOS(telnet, 관리 계정)와 pfSense(SSH 키)."""
import re
import socket
import subprocess
import threading
import time

import config

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
IOS_PROMPT = re.compile(rb"\r?\n?[\w\-.()]+[#>]\s*$")
_locks = {name: threading.Lock() for name in config.DEVICES}   # 장비당 동시 세션 1개


class DeviceError(Exception):
    pass


class _Telnet:
    def __init__(self, host, timeout=8):
        self.sock = socket.create_connection((host, 23), timeout=timeout)
        self.sock.settimeout(0.5)

    def _filter(self, data):
        """telnet 협상을 걸러 내고, 장비가 요청한 옵션은 모두 거절한다."""
        out, i = bytearray(), 0
        while i < len(data):
            b = data[i]
            if b == IAC and i + 1 < len(data):
                cmd = data[i + 1]
                if cmd in (DO, DONT, WILL, WONT) and i + 2 < len(data):
                    opt = data[i + 2]
                    reply = WONT if cmd in (DO, DONT) else DONT
                    self.sock.sendall(bytes([IAC, reply, opt]))
                    i += 3
                elif cmd == SB:
                    end = data.find(bytes([IAC, SE]), i)
                    i = len(data) if end < 0 else end + 2
                else:
                    i += 2
            else:
                out.append(b)
                i += 1
        return bytes(out)

    def read_until(self, pattern, timeout):
        buf, end = b"", time.time() + timeout
        while time.time() < end:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += self._filter(chunk)
            if pattern.search(buf):
                return buf.decode(errors="replace")
        raise DeviceError(f"응답 대기 시간 초과. 마지막 출력: {buf[-200:].decode(errors='replace')!r}")

    def send(self, line):
        self.sock.sendall(line.encode() + b"\r\n")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def ios_run(device, commands, timeout=20):
    """IOS 장비에 로그인해서 명령을 차례로 실행하고, 명령별 출력을 돌려준다."""
    info = config.DEVICES[device]
    user = config.read_secret("netops_user", "netops")
    pw = config.read_secret("netops_password")
    if not pw:
        raise DeviceError("장비 관리 계정 비밀번호가 설정되지 않았습니다 (secrets/netops_password).")
    with _locks[device]:
        try:
            t = _Telnet(info["host"])
        except OSError as e:
            raise DeviceError(f"{device}({info['host']}) 접속 실패: {e}")
        try:
            t.read_until(re.compile(rb"Username:\s*$"), 10)
            t.send(user)
            t.read_until(re.compile(rb"Password:\s*$"), 10)
            t.send(pw)
            first = t.read_until(re.compile(rb"([#>]\s*$)|(% Login invalid)|(Authentication failed)"), 10)
            if "invalid" in first or "failed" in first:
                raise DeviceError(f"{device} 로그인 실패")
            t.send("terminal length 0")
            t.read_until(IOS_PROMPT, 10)
            results = []
            for cmd in commands:
                t.send(cmd)
                out = t.read_until(IOS_PROMPT, timeout)
                lines = out.replace("\r", "").split("\n")
                body = "\n".join(lines[1:-1]).strip("\n")   # 에코된 명령과 마지막 프롬프트 제거
                results.append((cmd, body))
            t.send("exit")
            return results
        finally:
            t.close()


def pfsense_run(command, timeout=30):
    """pfSense에 에이전트 전용 SSH 키로 접속해 셸 명령 하나를 실행한다."""
    host = config.DEVICES["FW"]["host"]
    try:
        r = subprocess.run(
            ["ssh", "-i", config.SSH_KEY, "-o", f"UserKnownHostsFile={config.SSH_KNOWN_HOSTS}",
             "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
             f"admin@{host}", command],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DeviceError(f"FW 명령이 {timeout}초 안에 끝나지 않았습니다: {command}")
    # 원격 명령도 255로 끝날 수 있으므로(pfctl 등) ssh 자신의 오류 메시지가 있을 때만 접속 실패로 본다.
    if r.returncode == 255 and re.search(r"ssh:|Permission denied|Connection|Host key", r.stderr):
        raise DeviceError(f"FW({host}) SSH 접속 실패: {r.stderr.strip()[-300:]}")
    return r.returncode, (r.stdout + r.stderr).strip()
