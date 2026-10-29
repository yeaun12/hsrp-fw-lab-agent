"""자동 감시: 포트 스캔, HSRP Active 변화, 장비 응답 없음, ISP 게이트웨이 장애를 감지해 경보를 올리고 에이전트에 넘긴다."""
import re
import threading
import time

import brain
import config
import state
import tools
import web_detect
from devices import DeviceError, ios_run, pfsense_run

HSRP_LINE = re.compile(r"^(\S+)\s+(\d+)\s+(\d+)\s+P?\s*(Active|Standby|Speak|Listen|Init)\s+(\S+)\s+(\S+)\s+(\S+)", re.M)
DOWN_GRACE_SEC = 45        # 이 시간 이상 계속 접속이 안 될 때만 장애로 본다 (HSRP/STP 전환 중 순간 끊김 무시)
BATCH_SEC = 15             # 짧은 시간에 몰린 경보는 모아서 에이전트에 한 번에 넘긴다
_last_hsrp_active = {}     # group -> "CORE1"/"CORE2"
_last_gw = {}
_fail_since = {}           # device -> 처음 실패한 시각
_down_alerted = set()
_pending = []
_pending_lock = threading.Lock()


def _raise(kind, severity, title, detail, key, analyze=True):
    rec = state.add_alert(kind, severity, title, detail, key)
    if rec and analyze:
        with _pending_lock:
            _pending.append(rec)


def _dispatch():
    """처음 경보가 들어온 뒤 BATCH_SEC 동안 모아서 에이전트에 한 메시지로 넘긴다."""
    while True:
        time.sleep(1)
        with _pending_lock:
            if not _pending or time.time() - _pending[0]["ts"] < BATCH_SEC:
                continue
            batch = list(_pending)
            _pending.clear()
        lines = [f"- #{a['id']} [{a['severity']}] {a['title']}\n  {a['detail']}" for a in batch]
        head = f"[감시 알림] 경보 {len(batch)}건 (최근 {BATCH_SEC}초 동안 함께 발생)" if len(batch) > 1 else "[감시 알림]"
        brain.submit(head + "\n" + "\n".join(lines), "alert")


def _device_result(dev, ok, err=None):
    with state.lock:
        state.status["devices"][dev] = {"up": ok, "checked": state.now(), **({"error": err} if err else {})}
    if ok:
        _fail_since.pop(dev, None)
        if dev in _down_alerted:
            _down_alerted.discard(dev)
            _raise("device_up", "info", f"{dev} 관리 접속 복구", f"{dev}에 다시 접속됩니다.", key=f"up:{dev}:{time.time()}",
                   analyze=False)
        return
    first = _fail_since.setdefault(dev, time.time())
    if time.time() - first >= DOWN_GRACE_SEC and dev not in _down_alerted:
        _down_alerted.add(dev)
        _raise("device_down", "high", f"{dev} 관리 접속 불가",
               f"{dev}({config.DEVICES[dev]['host']})에 {int(time.time() - first)}초째 접속하지 못하고 있습니다: {err}",
               key=f"down:{dev}:{first}")


def _poll_firewall():
    rows = tools.firewall_blocked_summary(config.SCAN_WINDOW_SEC)
    blocked = tools.firewall_blocked_ips()
    with state.lock:
        state.status["blocked_ips"] = blocked
        state.status["fw_top_sources"] = rows[:5]
    for r in rows:
        if r["src"] in blocked or r["src"].startswith("10."):
            continue
        if r["distinct_targets"] >= config.SCAN_MIN_PORTS:
            _raise("port_scan", "high", f"외부 포트 스캔 의심: {r['src']}",
                   f"최근 {config.SCAN_WINDOW_SEC // 60}분 동안 {r['src']}에서 온 패킷 {r['blocked_packets']}개를 방화벽이 차단했고, "
                   f"서로 다른 대상 {r['distinct_targets']}개(예: {', '.join(r['targets'][:8])})를 두드렸습니다. "
                   f"대상 IP: {', '.join(r['dst_ips'])}.", key=f"scan:{r['src']}")


def _poll_webattacks():
    try:
        rows = web_detect.scan(lines=300)
    except Exception as e:                 # WEB 로그 수집 실패는 조용히 넘긴다(웹 앱이 없을 수도 있음)
        state.log_audit("monitor", "error", f"webattack: {type(e).__name__}: {e}")
        return
    with state.lock:
        state.status["web_attacks"] = [{"src": r["src"], "attempts": r["attempts"],
                                        "categories": r["categories"], "success": bool(r["success_hints"])} for r in rows[:5]]
    for r in rows:
        if r["attempts"] < 3:              # 산발적 요청은 경보로 올리지 않음
            continue
        sev = "high" if r["success_hints"] else "medium"
        cats = "·".join(r["categories"])
        detail = (f"{r['src']}에서 웹 서버({web_detect.WEB_HOST})로 {cats} 공격 시도 {r['attempts']}건. "
                  f"탐지 시그니처: {', '.join(r['signatures'])}.")
        if r["success_hints"]:
            detail += " 성공 가능성: " + "; ".join(r["success_hints"])
        _raise("web_attack", sev, f"웹 공격 탐지: {r['src']} ({cats})", detail, key=f"webatk:{r['src']}:{sev}")


def _poll_gateways():
    _, out = pfsense_run("pfSsh.php playback gatewaystatus")
    gws = {}
    for line in out.splitlines()[1:]:
        f = line.split()
        if len(f) >= 7:
            gws[f[0]] = {"monitor": f[1], "loss": f[5], "status": f[6]}
    with state.lock:
        state.status["gateways"] = gws
    _device_result("FW", True)
    for name, g in gws.items():
        prev = _last_gw.get(name)
        if prev and prev != g["status"]:
            sev = "high" if g["status"] != "online" else "info"
            _raise("gateway", sev, f"FW 게이트웨이 {name} 상태 변화: {prev} → {g['status']}",
                   f"pfSense 게이트웨이 {name}(모니터 {g['monitor']})의 상태가 {prev}에서 {g['status']}로 바뀌었습니다. "
                   f"손실률 {g['loss']}.", key=f"gw:{name}:{g['status']}")
        _last_gw[name] = g["status"]


def _poll_hsrp():
    table = {}
    for core in ("CORE1", "CORE2"):
        try:
            (_, out), = ios_run(core, ["show standby brief"])
            _device_result(core, True)
        except DeviceError as e:
            _device_result(core, False, str(e))
            continue
        for m in HSRP_LINE.finditer(out):
            iface, grp, prio, st = m.group(1), m.group(2), m.group(3), m.group(4)
            g = table.setdefault(grp, {"interface": iface, "vip": m.group(7), "members": {}})
            g["members"][core] = {"state": st, "priority": int(prio)}
    for grp, g in table.items():
        active = [c for c, v in g["members"].items() if v["state"] == "Active"]
        g["active"] = active[0] if len(active) == 1 else ("둘 다" if len(active) > 1 else "없음")
        prev = _last_hsrp_active.get(grp)
        if prev and prev != g["active"]:
            _raise("hsrp", "high" if g["active"] in ("없음", "둘 다") else "medium",
                   f"HSRP 그룹 {grp}({g['vip']}) Active 변경: {prev} → {g['active']}",
                   f"그룹 {grp}의 Active가 {prev}에서 {g['active']}로 바뀌었습니다. 현재 상태: "
                   + ", ".join(f"{c}={v['state']}(우선순위 {v['priority']})" for c, v in g["members"].items()),
                   key=f"hsrp:{grp}:{g['active']}")
        _last_hsrp_active[grp] = g["active"]
    with state.lock:
        state.status["hsrp"] = table


def _poll_access():
    for dev in ("ASW1", "ASW2", "BRANCH"):
        try:
            ios_run(dev, ["show clock"], timeout=10)
            _device_result(dev, True)
        except DeviceError as e:
            _device_result(dev, False, str(e))


def _loop(fn, period, name):
    while True:
        try:
            fn()
        except DeviceError as e:
            if name in ("firewall", "gateways"):
                _device_result("FW", False, str(e))
        except Exception as e:
            state.log_audit("monitor", "error", f"{name}: {type(e).__name__}: {e}")
        with state.lock:
            state.status["updated"] = state.now()
        time.sleep(period)


def start():
    threading.Thread(target=_dispatch, daemon=True, name="mon-dispatch").start()
    for fn, period, name in ((_poll_firewall, config.POLL_FIREWALL_SEC, "firewall"),
                             (_poll_webattacks, config.POLL_FIREWALL_SEC, "webattack"),
                             (_poll_gateways, config.POLL_NETWORK_SEC, "gateways"),
                             (_poll_hsrp, config.POLL_NETWORK_SEC, "hsrp"),
                             (_poll_access, config.POLL_NETWORK_SEC * 3, "access")):
        threading.Thread(target=_loop, args=(fn, period, name), daemon=True, name=f"mon-{name}").start()
