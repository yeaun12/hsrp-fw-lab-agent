"""GNS3 REST API와 장비 콘솔을 에이전트 도구로 감싼 모듈.

에이전트는 node_id 같은 내부 ID 대신 "R1", "Gi0/0" 같은 이름만 다루고,
이름과 ID의 대응은 이 모듈이 기억합니다.
"""
import asyncio
import json
import os
import re
import tempfile
from typing import Annotated, Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

import config

BASE_URL = f"http://{config.GNS3_HOST}:{config.GNS3_PORT}/{config.GNS3_API_VERSION}"

# 현재 작업 중인 프로젝트 상태
state: dict[str, Any] = {"project_id": None, "nodes": {}}  # nodes: name -> node json

_token: str | None = None
# 테스트에서 가짜 서버를 끼워 넣을 수 있도록 transport를 바꿀 수 있게 둡니다.
transport: httpx.AsyncBaseTransport | None = None


# ---------------------------------------------------------------- helpers

def _text(data: Any) -> dict:
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": data}]}


def _error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {msg}"}], "is_error": True}


async def _client() -> httpx.AsyncClient:
    global _token
    headers = {}
    if config.GNS3_API_VERSION == "v3" and config.GNS3_USER:
        if _token is None:
            async with httpx.AsyncClient(base_url=BASE_URL, transport=transport) as c:
                r = await c.post("/access/users/authenticate",
                                 json={"username": config.GNS3_USER,
                                       "password": config.GNS3_PASSWORD})
                r.raise_for_status()
                _token = r.json()["access_token"]
        headers["Authorization"] = f"Bearer {_token}"
    return httpx.AsyncClient(base_url=BASE_URL, headers=headers,
                             timeout=120, transport=transport)


async def _request(method: str, path: str, **kwargs) -> Any:
    async with await _client() as c:
        r = await c.request(method, path, **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else None


def _require_project() -> str:
    if not state["project_id"]:
        raise RuntimeError("열린 프로젝트가 없습니다. 먼저 create_project를 호출하세요.")
    return state["project_id"]


def _find_port(node: dict, interface: str) -> dict:
    wanted = interface.lower().replace(" ", "")
    for p in node.get("ports", []):
        for key in ("name", "short_name"):
            if (p.get(key) or "").lower().replace(" ", "") == wanted:
                return p
    names = [p.get("name") for p in node.get("ports", [])]
    raise RuntimeError(f"{node['name']}에 '{interface}' 포트가 없습니다. 사용 가능한 포트: {names}")


# ---------------------------------------------------------------- console

IAC = 255
# 장비 프롬프트(R1#, R1(config)#, user@host:~$) 또는 리눅스 로그인 프롬프트(login:, Password:)
PROMPT_RE = re.compile(rb"([\w.\-()/@:~]+[>#$%]|(?i:login|username|password):)\s*$")


def _strip_telnet(data: bytes) -> bytes:
    """telnet 협상 바이트(IAC ...)를 제거한다."""
    out, i = bytearray(), 0
    while i < len(data):
        if data[i] == IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd == 250:  # SB ... SE
                end = data.find(bytes([IAC, 240]), i)
                i = len(data) if end < 0 else end + 2
            elif cmd == IAC:
                out.append(IAC)
                i += 2
            else:
                i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


async def _read_until_prompt(reader: asyncio.StreamReader, timeout: float) -> tuple[str, bool]:
    buf = b""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=1)
        except asyncio.TimeoutError:
            chunk = b""
        if chunk:
            buf += _strip_telnet(chunk)
            if b"--More--" in buf[-20:]:
                continue
            if PROMPT_RE.search(buf.rstrip(b"\r\n ") + b" "):
                return buf.decode(errors="replace"), True
    return buf.decode(errors="replace"), False


async def _open_console(node_name: str):
    node = state["nodes"].get(node_name)
    if node is None:
        raise RuntimeError(f"'{node_name}' 노드를 모릅니다. 알고 있는 노드: {list(state['nodes'])}")
    if not node.get("console"):
        raise RuntimeError(f"{node_name}에는 콘솔 포트가 없습니다.")
    return await asyncio.open_connection(config.GNS3_HOST, node["console"])


# ---------------------------------------------------------------- tools

async def _host_vms() -> dict[str, list]:
    """PC에 설치된 VirtualBox / VMware VM 목록. 설치되지 않은 쪽은 빈 목록."""
    found = {"virtualbox": [], "vmware": []}
    for vm_type in found:
        try:
            found[vm_type] = await _request("GET", f"/computes/local/{vm_type}/vms")
        except Exception:
            pass
    return found


async def get_resources() -> dict:
    """기획 에이전트에게 알려 줄, 지금 쓸 수 있는 템플릿과 VM 목록."""
    templates = await _request("GET", "/templates")
    vms = await _host_vms()
    return {
        "templates": [t["name"] for t in templates if not t.get("builtin")] +
                     [t["name"] for t in templates if t.get("builtin")],
        "virtualbox_vms": [v["vmname"] for v in vms["virtualbox"]],
        "vmware_vms": [v["vmname"] for v in vms["vmware"]],
    }


@tool("list_host_vms", "PC에 설치된 VirtualBox / VMware VM 목록을 조회한다.", {})
async def list_host_vms(args):
    try:
        vms = await _host_vms()
        return _text({k: [v["vmname"] for v in lst] for k, lst in vms.items()})
    except Exception as e:
        return _error(str(e))


@tool("register_vm_template",
      "PC에 설치된 VM을 GNS3 템플릿으로 등록한다. 같은 이름의 템플릿이 있으면 그대로 둔다. "
      "linked clone으로 등록하므로 원본 VM은 바뀌지 않는다.",
      {"template_name": str,
       "vm_type": Annotated[str, "virtualbox 또는 vmware"],
       "vm_name": Annotated[str, "list_host_vms에 나온 VM 이름"],
       "adapters": Annotated[int, "GNS3에 연결할 네트워크 어댑터 수"]})
async def register_vm_template(args):
    try:
        vm_type = args["vm_type"].lower()
        if vm_type not in ("virtualbox", "vmware"):
            return _error("vm_type은 virtualbox 또는 vmware여야 합니다.")
        templates = await _request("GET", "/templates")
        if any(t["name"] == args["template_name"] for t in templates):
            return _text(f"템플릿 '{args['template_name']}'은 이미 있습니다.")
        vms = (await _host_vms())[vm_type]
        vm = next((v for v in vms if v["vmname"] == args["vm_name"]), None)
        if vm is None:
            return _error(f"{vm_type} VM '{args['vm_name']}'이 없습니다. 있는 VM: {[v['vmname'] for v in vms]}")
        body = {"name": args["template_name"], "template_type": vm_type, "compute_id": "local",
                "adapters": args["adapters"], "linked_clone": True, "console_type": "telnet"}
        if vm_type == "virtualbox":
            body["vmname"] = vm["vmname"]
        else:
            body["vmx_path"] = vm["vmx_path"]
        await _request("POST", "/templates", json=body)
        return _text(f"'{args['vm_name']}'을 템플릿 '{args['template_name']}'으로 등록했습니다.")
    except Exception as e:
        return _error(str(e))


@tool("list_templates", "GNS3 서버에 등록된 장비 템플릿 목록을 조회한다.", {})
async def list_templates(args):
    try:
        templates = await _request("GET", "/templates")
        return _text([{"name": t["name"], "type": t.get("template_type")} for t in templates])
    except Exception as e:
        return _error(str(e))


@tool("create_project",
      "새 GNS3 프로젝트를 만들고 이후 작업 대상으로 연다. 같은 이름이 있으면 그 프로젝트를 연다.",
      {"name": str})
async def create_project(args):
    try:
        projects = await _request("GET", "/projects")
        existing = next((p for p in projects if p["name"] == args["name"]), None)
        if existing:
            pid = existing["project_id"]
            await _request("POST", f"/projects/{pid}/open")
            nodes = await _request("GET", f"/projects/{pid}/nodes")
            state.update(project_id=pid, nodes={n["name"]: n for n in nodes})
            return _text(f"기존 프로젝트를 열었습니다. 이미 있는 노드: {list(state['nodes'])}")
        project = await _request("POST", "/projects", json={"name": args["name"]})
        state.update(project_id=project["project_id"], nodes={})
        return _text(f"프로젝트 '{args['name']}' 생성 완료")
    except Exception as e:
        return _error(str(e))


@tool("add_node", "템플릿 이름으로 노드를 추가한다. 결과로 해당 노드의 포트(인터페이스) 목록을 돌려준다.",
      {"name": str,
       "template": Annotated[str, "list_templates에 나온 템플릿 이름"],
       "x": int, "y": int})
async def add_node(args):
    try:
        pid = _require_project()
        if args["name"] in state["nodes"]:
            return _error(f"{args['name']} 노드는 이미 있습니다.")
        templates = await _request("GET", "/templates")
        tpl = next((t for t in templates if t["name"] == args["template"]), None)
        if tpl is None:
            return _error(f"템플릿 '{args['template']}'이 없습니다. 있는 템플릿: {[t['name'] for t in templates]}")
        # 처음부터 원하는 이름으로 만든다. VirtualBox linked clone은 나중에 이름을 바꾸면
        # VM 이름까지 바뀌므로, 서버가 이름을 무시했을 때만 PUT으로 바꾼다.
        node = await _request("POST", f"/projects/{pid}/templates/{tpl['template_id']}",
                              json={"x": args["x"], "y": args["y"], "compute_id": "local",
                                    "name": args["name"]})
        if node["name"] != args["name"]:
            node = await _request("PUT", f"/projects/{pid}/nodes/{node['node_id']}",
                                  json={"name": args["name"]})
        state["nodes"][node["name"]] = node
        return _text({"name": node["name"],
                      "ports": [p["name"] for p in node.get("ports", [])]})
    except Exception as e:
        return _error(str(e))


@tool("add_link", "두 노드의 인터페이스를 케이블로 연결한다. 인터페이스는 add_node가 알려준 포트 이름을 쓴다.",
      {"node_a": str, "interface_a": str, "node_b": str, "interface_b": str})
async def add_link(args):
    try:
        pid = _require_project()
        ends = []
        for n_key, i_key in (("node_a", "interface_a"), ("node_b", "interface_b")):
            node = state["nodes"].get(args[n_key])
            if node is None:
                return _error(f"'{args[n_key]}' 노드를 모릅니다.")
            port = _find_port(node, args[i_key])
            ends.append({"node_id": node["node_id"],
                         "adapter_number": port["adapter_number"],
                         "port_number": port["port_number"]})
        await _request("POST", f"/projects/{pid}/links", json={"nodes": ends})
        return _text(f"{args['node_a']} {args['interface_a']} <-> {args['node_b']} {args['interface_b']} 연결 완료")
    except Exception as e:
        return _error(str(e))


@tool("start_all_nodes", "프로젝트의 모든 노드를 켠다.", {})
async def start_all_nodes(args):
    try:
        pid = _require_project()
        await _request("POST", f"/projects/{pid}/nodes/start")
        nodes = await _request("GET", f"/projects/{pid}/nodes")
        state["nodes"] = {n["name"]: n for n in nodes}
        return _text({n["name"]: n["status"] for n in nodes})
    except Exception as e:
        return _error(str(e))


@tool("project_status", "현재 프로젝트의 노드 상태와 링크 목록을 조회한다.", {})
async def project_status(args):
    try:
        pid = _require_project()
        nodes = await _request("GET", f"/projects/{pid}/nodes")
        links = await _request("GET", f"/projects/{pid}/links")
        state["nodes"] = {n["name"]: n for n in nodes}
        by_id = {n["node_id"]: n["name"] for n in nodes}
        return _text({
            "nodes": {n["name"]: n["status"] for n in nodes},
            "links": [" <-> ".join(f"{by_id.get(e['node_id'])}"
                                   f"({e.get('label', {}).get('text', e['adapter_number'])})"
                                   for e in l["nodes"]) for l in links],
        })
    except Exception as e:
        return _error(str(e))


@tool("wait_for_boot",
      "노드 콘솔에 프롬프트가 뜰 때까지(부팅 완료) 기다린다. 초기 설정 대화상자가 나오면 자동으로 건너뛴다.",
      {"node": str})
async def wait_for_boot(args):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.BOOT_TIMEOUT_SEC
    last = ""
    while loop.time() < deadline:
        try:
            reader, writer = await _open_console(args["node"])
            try:
                writer.write(b"\r\n")
                out, ok = await _read_until_prompt(reader, 10)
                last = out
                if "initial configuration dialog" in out or "[yes/no]" in out:
                    writer.write(b"no\r\n")
                    await asyncio.sleep(5)
                    writer.write(b"\r\n")
                    out, ok = await _read_until_prompt(reader, 30)
                    last = out
                if ok:
                    return _text(f"{args['node']} 부팅 완료. 현재 프롬프트: {out.strip().splitlines()[-1]}")
            finally:
                writer.close()
        except RuntimeError as e:
            return _error(str(e))
        except OSError:
            pass
        await asyncio.sleep(10)
    return _error(f"{config.BOOT_TIMEOUT_SEC}초 안에 {args['node']} 부팅을 확인하지 못했습니다. "
                  f"마지막 출력: {last[-500:]}")


@tool("console_send",
      "노드 콘솔에 명령을 한 줄씩 보내고 출력을 돌려준다. 설정은 'enable', 'configure terminal'부터 직접 보내야 한다.",
      {"node": str, "commands": list[str]})
async def console_send(args):
    try:
        reader, writer = await _open_console(args["node"])
    except Exception as e:
        return _error(str(e))
    transcript = []
    try:
        writer.write(b"\r\n")
        await _read_until_prompt(reader, config.COMMAND_TIMEOUT_SEC)
        for cmd in args["commands"]:
            writer.write(cmd.encode() + b"\r\n")
            out, ok = await _read_until_prompt(reader, config.COMMAND_TIMEOUT_SEC)
            if "--More--" in out:
                writer.write(b" ")
            if not ok:
                out += "\n[경고: 프롬프트가 돌아오지 않음 - 명령이 아직 실행 중이거나 응답이 없음]"
            transcript.append(out)
            if "% Invalid" in out or "% Incomplete" in out or "% Ambiguous" in out:
                transcript.append(f"[오류 발생: '{cmd}' 이후 명령은 보내지 않았습니다]")
                break
    finally:
        writer.close()
    text = "".join(transcript)
    return _text(text[-6000:])


# ---------------------------------------------------------------- VirtualBox 게스트 제어
# Guest Additions가 설치된 VM 안에서 VBoxManage guestcontrol로 명령을 실행한다.
# 시리얼 콘솔이 없어도 되고, Windows VM도 설정할 수 있다.

async def _vbox(*args: str, timeout: float = 120) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        config.VBOXMANAGE, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, f"[{timeout:.0f}초 안에 끝나지 않아 중단했습니다]"
    return proc.returncode, out.decode(errors="replace")


_vbox_major: int | None = None


async def _vbox_version() -> int:
    """VBoxManage 주 버전 (6, 7, ...). 알 수 없으면 7로 본다."""
    global _vbox_major
    if _vbox_major is None:
        code, out = await _vbox("--version", timeout=30)
        m = re.match(r"\s*(\d+)\.", out)
        _vbox_major = int(m.group(1)) if code == 0 and m else 7
    return _vbox_major


async def _vm_info(node_name: str) -> tuple[str, tuple[str, str]]:
    """노드의 VirtualBox VM 이름(복제본)과 로그인 계정을 찾는다."""
    node = state["nodes"].get(node_name)
    if node is None:
        raise RuntimeError(f"'{node_name}' 노드를 모릅니다. 알고 있는 노드: {list(state['nodes'])}")
    if node.get("node_type") not in (None, "virtualbox"):
        raise RuntimeError(f"{node_name}은 VirtualBox 노드가 아닙니다. 장비는 console_send를 쓰세요.")
    vmname = (node.get("properties") or {}).get("vmname") or node.get("vmname") or node_name
    # VM별 계정(config.VM_CREDENTIALS)이 있으면 그걸 쓰고, 없으면 공통 계정을 쓴다.
    base = None
    try:
        templates = await _request("GET", "/templates")
        tpl = next((t for t in templates if t.get("template_id") == node.get("template_id")), None)
        base = tpl and tpl.get("vmname")
    except Exception:
        pass
    creds = (config.VM_CREDENTIALS.get(base) or config.VM_CREDENTIALS.get(node_name)
             or (config.VM_USERNAME, config.VM_PASSWORD))
    return vmname, creds


async def _guest_os(vmname: str) -> str | None:
    code, out = await _vbox("guestproperty", "get", vmname, "/VirtualBox/GuestInfo/OS/Product", timeout=30)
    if code != 0 or "Value:" not in out:
        return None
    return out.split("Value:", 1)[1].strip()


async def _guest_run(vmname: str, creds: tuple[str, str], os_name: str, command: str,
                     timeout_sec: int) -> tuple[int, str]:
    if os_name.lower().startswith("windows"):
        exe, argv = r"C:\Windows\System32\cmd.exe", ["cmd.exe", "/c", command]
    else:
        if command.startswith("sudo "):
            # sudo 비밀번호를 표준 입력으로 넘긴다.
            pw = creds[1].replace("'", "'\\''")
            command = f"printf '%s\\n' '{pw}' | sudo -S -p '' " + command[len("sudo "):]
        exe, argv = "/bin/sh", ["/bin/sh", "-c", command]
    # VirtualBox 6.x는 "--" 뒤 첫 인자를 argv[0]으로 쓰지만, 7.x는 --exe가 argv[0]이 되고
    # "--" 뒤는 argv[1]부터다. 7.x에 argv[0]을 또 넘기면 sh가 /bin/sh를 스크립트로 읽어 실패한다.
    if await _vbox_version() >= 7:
        argv = argv[1:]
    # 비밀번호가 프로세스 목록에 보이지 않도록 파일로 넘긴다.
    fd, pwfile = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "w") as f:
            f.write(creds[1])
        return await _vbox("guestcontrol", vmname, "run", "--exe", exe,
                           "--username", creds[0], "--passwordfile", pwfile,
                           "--wait-stdout", "--wait-stderr",
                           "--timeout", str(timeout_sec * 1000), "--", *argv,
                           timeout=timeout_sec + 30)
    finally:
        os.unlink(pwfile)


@tool("wait_for_guest",
      "VirtualBox VM 노드의 OS가 부팅되고 Guest Additions가 응답할 때까지 기다린다. 게스트 OS 이름을 돌려준다.",
      {"node": str})
async def wait_for_guest(args):
    try:
        vmname, creds = await _vm_info(args["node"])
    except Exception as e:
        return _error(str(e))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.BOOT_TIMEOUT_SEC
    last = ""
    while loop.time() < deadline:
        os_name = await _guest_os(vmname)
        if os_name:
            code, out = await _guest_run(vmname, creds, os_name,
                                         "echo ready", timeout_sec=30)
            if code == 0 and "ready" in out:
                return _text(f"{args['node']} 준비 완료 (게스트 OS: {os_name})")
            last = out
        await asyncio.sleep(10)
    return _error(f"{config.BOOT_TIMEOUT_SEC}초 안에 {args['node']}의 Guest Additions 응답을 받지 못했습니다. "
                  f"Guest Additions 설치 여부와 계정 정보를 확인해야 합니다. 마지막 출력: {last[-500:]}")


@tool("vm_exec",
      "VirtualBox VM 노드 안에서 셸 명령을 실행한다 (리눅스: /bin/sh -c, Windows: cmd.exe /c). "
      "명령마다 따로 실행되며, 종료 코드와 출력을 돌려준다.",
      {"node": str, "commands": list[str]})
async def vm_exec(args):
    try:
        vmname, creds = await _vm_info(args["node"])
        os_name = await _guest_os(vmname)
        if not os_name:
            return _error(f"{args['node']}의 Guest Additions가 응답하지 않습니다. 먼저 wait_for_guest를 호출하세요.")
    except Exception as e:
        return _error(str(e))
    results = []
    for cmd in args["commands"]:
        code, out = await _guest_run(vmname, creds, os_name, cmd, config.COMMAND_TIMEOUT_SEC * 4)
        results.append(f"$ {cmd}\n{out.strip()}\n[종료 코드 {code}]")
        if code != 0:
            results.append(f"[오류 발생: '{cmd}' 이후 명령은 실행하지 않았습니다]")
            break
    return _text("\n\n".join(results)[-6000:])


@tool("wait_seconds", "라우팅 수렴 등을 위해 지정한 초만큼 기다린다 (최대 300초).", {"seconds": int})
async def wait_seconds(args):
    seconds = max(1, min(int(args["seconds"]), 300))
    await asyncio.sleep(seconds)
    return _text(f"{seconds}초 기다렸습니다.")


ALL_TOOLS = [list_templates, list_host_vms, register_vm_template, create_project, add_node,
             add_link, start_all_nodes, project_status, wait_for_boot, console_send,
             wait_for_guest, vm_exec, wait_seconds]

server = create_sdk_mcp_server(name="gns3", version="1.0.0", tools=ALL_TOOLS)
TOOL_NAMES = [f"mcp__gns3__{t.name}" for t in ALL_TOOLS]
