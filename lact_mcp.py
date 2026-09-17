#!/usr/bin/env python3
"""MCP server (stdlib only, no deps) to control GPUs via LACT on Linux.

Transport: JSON-RPC 2.0 over stdio, one JSON object per line (MCP stdio).
Backend: `lact cli` subprocess + JSON socket /run/lactd.sock for advanced calls.

Tools (10): list_gpus, gpu_info, gpu_stats, power, profiles, auto_switch,
           gpu_config_get, fan, clocks, daemon_query.
"""
import json
import os
import re
import socket
import subprocess
import sys

VERSION = "0.2.1"
SOCKETS = ["/run/lactd.sock", "/var/run/lactd.sock",
           f"/run/user/{os.getuid()}/lactd.sock"]
LACT = ["lact", "cli"]


def cli(*args, timeout=15):
    """Run `lact cli ...`, return stdout (raises RuntimeError on failure)."""
    try:
        p = subprocess.run(LACT + list(args), capture_output=True,
                           text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("lact binary not found (is LACT installed?)")
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip() or f"lact cli exited {p.returncode}"
        if msg.startswith("Error: "):  # lact prefixes already; err() adds its own
            msg = msg[len("Error: "):]
        raise RuntimeError(msg)
    return p.stdout.strip()


def with_gpu(gpu_id, *cmd):
    a = ["--gpu-id", str(gpu_id)] if gpu_id not in (None, "") else []
    return cli(*a, *cmd)


def sock_query(command, args=None, timeout=10):
    """Send one JSON request to lactd socket, return decoded response dict."""
    msg = {"command": command}
    if args is not None:
        msg["args"] = args
    payload = (json.dumps(msg) + "\n").encode()
    last = None
    for path in SOCKETS:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(path)
            s.sendall(payload)
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
                try:
                    return json.loads(buf.decode())
                except (ValueError, UnicodeDecodeError):
                    continue
            raise RuntimeError(f"empty reply from {path}")
        except (FileNotFoundError, ConnectionRefusedError,
                PermissionError, OSError) as e:
            last = f"{path}: {e}"
    raise RuntimeError(f"lactd socket unreachable ({last}). "
                       "Is lactd running? user in wheel/admin_group?")


def ok(text):
    return {"content": [{"type": "text", "text": text}]}


def err(text):
    return {"content": [{"type": "text", "text": f"Error: {text}"}],
            "isError": True}


# --- tool implementations (all take dict args, return str) ---

def t_list_gpus(a):
    return with_gpu(None, "list")


def t_gpu_info(a):
    return with_gpu(resolve_id(a.get("gpu_id", "0")), "info")


def t_gpu_stats(a):
    return with_gpu(resolve_id(a.get("gpu_id", "0")), "stats")


def t_power(a):
    action = a.get("action", "get")
    if action == "get":
        # lact cli --gpu-id takes index or full ID, not PCI fragments
        return with_gpu(resolve_id(a.get("gpu_id") or "0"),
                        "power-limit", "get")
    if action == "set":
        gid = target_gpu(a, write=True)
        watts = a.get("watts")
        if watts is None:
            raise ValueError("power set requires 'watts'")
        cur = with_gpu(gid, "power-limit", "get")
        m = re.search(r"(\d+(?:\.\d+)?)W to (\d+(?:\.\d+)?)W", cur)
        if m and not (float(m.group(1)) <= float(watts) <= float(m.group(2))):
            raise ValueError(f"{watts}W outside configurable range "
                             f"{m.group(1)}W-{m.group(2)}W (refused)")
        out = with_gpu(gid, "power-limit", "set", str(watts))
        return f"{out}\n(was: {cur})"
    raise ValueError("action must be get|set")


def t_profiles(a):
    action = a.get("action", "list")
    if action in ("list", "get"):
        return cli("profile", action)
    if action == "set":
        name = a.get("name")
        if not name:
            raise ValueError("profiles set requires 'name'")
        return cli("profile", "set", name)
    raise ValueError("action must be list|get|set")


def t_auto_switch(a):
    action = a.get("action", "get")
    if action not in ("get", "enable", "disable"):
        raise ValueError("action must be get|enable|disable")
    return cli("profile", "auto-switch", action)


def resolve_id(gpu_id):
    """Index ('1'), PCI suffix ('0000:01:00.0') or full LACT ID -> full ID."""
    gid = str(gpu_id)
    devs = (sock_query("list_devices")["data"]) or []
    ids = [d["id"] for d in devs if "id" in d]
    if gid in ids:
        return gid
    if ":" in gid:  # PCI fragment: unique suffix match
        matches = [i for i in ids if i.endswith(gid)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ValueError(f"ambiguous gpu {gid!r}: matches {matches}")
    else:
        try:
            idx = int(gid)
            if idx < 0:
                raise ValueError("negative index")
            return devs[idx]["id"]
        except (IndexError, ValueError, KeyError, TypeError):
            pass
    raise ValueError(f"unknown gpu {gid!r} (see list_gpus)")


def target_gpu(a, write):
    """Reads default to GPU 0; writes refuse a default when >1 GPU exists
    (too easy to hit the wrong card, e.g. the iGPU)."""
    gid = a.get("gpu_id")
    if gid is None or gid == "":
        n = len((sock_query("list_devices")["data"]) or [])
        if write and n > 1:
            raise ValueError(
                f"ambiguous: {n} GPUs, specify gpu_id explicitly "
                f"(see list_gpus). Refusing to write to a default GPU.")
        gid = "0"
    return resolve_id(gid)


def _daemon_err(r, hint=""):
    text = json.dumps(r)
    if "missing field" in text:
        text += (" Hint: " + hint) if hint else \
            " Hint: this command needs args (e.g. {\"id\": \"<gpu-id>\"})."
    return text


def confirmed_write(command, args):
    """Socket write + immediate confirm. MCP is non-interactive: without
    confirm, lactd auto-reverts after ~5s (apply_settings_timer)."""
    r = sock_query(command, args)
    if r.get("status") != "ok":
        raise RuntimeError(_daemon_err(
            r, "socket write rejected; check the value against clocks get."))
    c = sock_query("confirm_pending_config", {"command": "confirm"})
    if c.get("status") != "ok":
        raise RuntimeError(f"applied but confirm failed: {json.dumps(c)}")
    return r


def _ratio(v, what, lo=0.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number (0..1 or 0..100%)")
    if 1 < v <= 100:  # percent shorthand
        v /= 100.0
    hi = 1.0
    if not lo <= v <= hi:
        lo_s = f"{lo:.0%}" if lo else "0"
        raise ValueError(f"{what} outside {lo_s}..100% (hardware minimum)")
    return v


def _fan_min_speed(gid):
    """Hardware minimum fan speed 0..1 from pwm_min/pwm_max (0 if unknown)."""
    try:
        st = sock_query("device_stats", {"id": gid})
        f = (st.get("data") or {}).get("fan", {})
        mn, mx = f.get("pwm_min"), f.get("pwm_max")
        if mn is not None and mx:
            return mn / mx
    except RuntimeError:
        pass
    return 0.0


def t_fan(a):
    action = a.get("action", "get")
    gid = target_gpu(a, write=(action != "get"))
    if action == "get":
        st = sock_query("device_stats", {"id": gid})
        if st.get("status") != "ok":
            raise RuntimeError(_daemon_err(st))
        st = st["data"]["fan"]
        cfg = sock_query("get_gpu_config", {"id": gid})["data"] or {}
        f = cfg.get("fan_control_settings") or {}
        mode = st.get("control_mode",
                      "automatic" if not st.get("control_enabled") else "?")
        note = ""
        if st.get("pwm_max") is None and not st.get("control_enabled"):
            note = " (no pwm range reported: fan control likely unsupported by this driver — e.g. NVIDIA proprietary)"
        return (f"mode: {mode} (enabled: {st.get('control_enabled')}), "
                f"speed: {st.get('speed_current')} RPM "
                f"(pwm {st.get('pwm_current')}), "
                f"curve: {st.get('curve') or f.get('curve')}{note}")
    if action == "auto":
        confirmed_write("set_fan_control", {"id": gid, "enabled": False})
        return f"{gid}: fan back to automatic"
    if action == "set":
        mode = a.get("mode", "curve")
        lo = _fan_min_speed(gid)
        if mode == "static":
            if a.get("speed") is None:
                raise ValueError("fan set static requires 'speed'")
            args = {"id": gid, "enabled": True, "mode": "static",
                    "static_speed": _ratio(a["speed"], "speed", lo)}
        elif mode == "curve":
            curve = a.get("curve")
            if not isinstance(curve, dict) or not curve:
                raise ValueError("fan set curve requires 'curve' {tempC: speed}")
            pts = {}
            for t, s in curve.items():
                try:
                    ti = int(t)
                except (TypeError, ValueError):
                    raise ValueError(f"bad curve temp {t!r}")
                if not 20 <= ti <= 120:
                    raise ValueError(f"curve temp {ti} outside 20..120")
                pts[str(ti)] = _ratio(s, f"curve[{ti}]", lo)
            if not 2 <= len(pts) <= 8:
                raise ValueError("curve needs 2..8 points")
            args = {"id": gid, "enabled": True, "mode": "curve", "curve": pts}
        else:
            raise ValueError("mode must be curve|static")
        confirmed_write("set_fan_control", args)
        return f"{gid}: fan {mode} applied+confirmed (restore with action=auto)"
    raise ValueError("action must be get|set|auto")


PERF_LEVELS = ("auto", "low", "high", "manual")
OFFSET_TYPES = (("gpu_offset", "gpu_offsets", "gpu_clock_offset"),
                ("mem_offset", "mem_offsets", "mem_clock_offset"))
CLOCK_RESET_KEYS = ("min_core_clock", "max_core_clock", "min_memory_clock",
                    "max_memory_clock", "voltage_offset", "voltage_boost",
                    "gpu_clock_offsets", "mem_clock_offsets")


def _clocks_dirty(cfg):
    """True if any clock override is set (lactd rejects resetting defaults)."""
    return any(cfg.get(k) not in (None, {}, []) for k in CLOCK_RESET_KEYS)


def t_clocks(a):
    action = a.get("action", "get")
    gid = target_gpu(a, write=(action != "get"))
    info = sock_query("device_clocks_info", {"id": gid})
    if info.get("status") != "ok":
        raise RuntimeError(_daemon_err(info))
    table = info["data"]["table"]["value"]
    if action == "get":
        cfg = sock_query("get_gpu_config", {"id": gid})["data"] or {}
        cur = {k: cfg[k] for k in ("min_core_clock", "max_core_clock",
               "min_memory_clock", "max_memory_clock", "voltage_offset",
               "voltage_boost", "performance_level") if cfg.get(k) is not None}
        ranges = {k: v for k, v in table.items() if "range" in k}
        lines = [
            f"ranges: {json.dumps(ranges) if ranges else '{} (no ranges reported — offsets below are authoritative)'}",
            f"gpu_offsets: {json.dumps(table.get('gpu_offsets', {}))}",
            f"mem_offsets: {json.dumps(table.get('mem_offsets', {}))}",
            f"config: {json.dumps(cur) if cur else 'defaults'}"]
        return "\n".join(lines)
    if action == "reset":
        cfg = sock_query("get_gpu_config", {"id": gid})["data"] or {}
        if not _clocks_dirty(cfg):
            return f"{gid}: already at defaults (nothing to reset)"
        confirmed_write("set_clocks_value",
                        {"id": gid, "command": {"type": "reset"}})
        return f"{gid}: clocks reset+confirmed"
    if action == "set":
        a = dict(a)
        if a.get("max_mem_clock") is not None and a.get("max_memory_clock") is None:
            a["max_memory_clock"] = a["max_mem_clock"]  # alias
        cmds = []
        for key in ("max_core_clock", "min_core_clock", "max_memory_clock",
                    "min_memory_clock", "min_voltage", "max_voltage",
                    "voltage_offset"):
            if a.get(key) is not None:
                try:
                    v = int(a[key])
                except (TypeError, ValueError):
                    raise ValueError(f"{key} must be an integer")
                cmds.append({"type": key, "value": v})
        for off_key, states_key, type_name in OFFSET_TYPES:
            if a.get(off_key) is not None:
                try:
                    v = int(a[off_key])
                except (TypeError, ValueError):
                    raise ValueError(f"{off_key} must be an integer (MHz)")
                states = table.get(states_key, {})
                if not states:
                    raise ValueError(f"{states_key} not supported on this GPU")
                for p, lim in states.items():
                    if not lim["min"] <= v <= lim["max"]:
                        raise ValueError(f"{off_key} {v} outside "
                                         f"{lim['min']}..{lim['max']} (pstate {p})")
                    cmds.append({"type": {type_name: int(p)}, "value": v})
        if a.get("voltage_boost") is not None:
            try:
                v = int(a["voltage_boost"])
            except (TypeError, ValueError):
                raise ValueError("voltage_boost must be an integer (%)")
            if not 0 <= v <= 100:
                raise ValueError("voltage_boost outside 0..100")
            cmds.append({"type": "voltage_boost", "value": v})
        perf = a.get("performance_level")
        if perf is not None and perf not in PERF_LEVELS:
            raise ValueError(f"performance_level must be one of {PERF_LEVELS}")
        if not cmds and perf is None:
            raise ValueError("nothing to set (max_core_clock, gpu_offset, ... or performance_level)")
        if len(cmds) == 1:
            confirmed_write("set_clocks_value",
                            {"id": gid, "command": cmds[0]})
        elif cmds:
            confirmed_write("batch_set_clocks_value",
                            {"id": gid, "commands": cmds})
        if perf is not None:
            confirmed_write("set_performance_level",
                            {"id": gid, "performance_level": perf})
        extra = f" + performance_level={perf}" if perf else ""
        return (f"{gid}: {len(cmds)} clock command(s){extra} applied+confirmed "
                f"(restore with action=reset)")
    raise ValueError("action must be get|set|reset")


def t_gpu_config_get(a):
    gid = resolve_id(a.get("gpu_id", "0"))
    r = sock_query("get_gpu_config", {"id": gid})
    if r.get("status") != "ok":
        raise RuntimeError(json.dumps(r))
    if r.get("data") is None:
        return f"{gid}: default config (no overrides)"
    return json.dumps(r["data"], indent=2)


AUTO_CONFIRM_WRITES = {"set_clocks_value", "batch_set_clocks_value",
                         "set_power_cap", "set_fan_control",
                         "set_performance_level", "set_power_profile_mode",
                         "set_enabled_power_states"}


def t_daemon_query(a):
    if not a.get("command"):
        raise ValueError("missing 'command' (e.g. device_stats, list_devices)")
    args = a.get("args")
    if isinstance(args, dict) and isinstance(args.get("id"), str):
        args = {**args, "id": resolve_id(args["id"])}  # index/PCI accepted
    r = sock_query(a["command"], args)
    if r.get("status") != "ok":
        raise RuntimeError(_daemon_err(r))
    if a["command"] in AUTO_CONFIRM_WRITES:
        c = sock_query("confirm_pending_config", {"command": "confirm"})
        if c.get("status") != "ok":
            return (json.dumps(r, indent=2) +
                    f"\nWARNING: applied but auto-confirm failed "
                    f"({json.dumps(c)}); settings revert in ~5s — "
                    f"call confirm_pending_config manually NOW.")
        return json.dumps(r, indent=2) + "\n(auto-confirmed, no 5s revert)"
    return json.dumps(r, indent=2)


TOOLS = [
    ("list_gpus", "List GPUs managed by LACT (index, PCI ID, model).",
     {"type": "object", "properties": {}}, t_list_gpus),
    ("gpu_info", "Static GPU info (model, driver, VRAM, VBIOS, PCIe).",
     {"type": "object", "properties": {
         "gpu_id": {"type": "string", "description": "GPU index or full PCI ID (default 0)"}}}, t_gpu_info),
    ("gpu_stats", "Live stats: clocks, voltage, power, temps, VRAM, fans, throttling.",
     {"type": "object", "properties": {
         "gpu_id": {"type": "string", "description": "GPU index or full PCI ID (default 0)"}}}, t_gpu_stats),
    ("power", "Get or set the GPU power cap. set validates the configurable range first.",
     {"type": "object", "properties": {
         "gpu_id": {"type": "string", "description": "REQUIRED for set when several GPUs (no silent default). Index, PCI or full ID."},
         "action": {"type": "string", "enum": ["get", "set"], "default": "get"},
         "watts": {"type": "number", "description": "Required for set."}},
      "required": ["action"]}, t_power),
    ("profiles", "List, show current, or apply a LACT profile.",
     {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "get", "set"], "default": "list"},
         "name": {"type": "string", "description": "Profile name (required for set)."}}}, t_profiles),
    ("auto_switch", "Get/enable/disable automatic profile switching.",
     {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["get", "enable", "disable"], "default": "get"}}}, t_auto_switch),
    ("gpu_config_get", "Full applied GPU config (fan curve, clocks, power cap...) via lactd socket. Null = defaults.",
     {"type": "object", "properties": {
         "gpu_id": {"type": "string", "description": "GPU index or full PCI ID (default 0)"}}}, t_gpu_config_get),
    ("daemon_query", "Raw lactd socket call (e.g. device_info, device_stats,"
     " device_clocks_info). id accepts index ('1'), PCI ('0000:01:00.0') or full LACT ID."
     " GPU-config writes (set_*) auto-confirm (no 5s revert).",
     {"type": "object", "properties": {
         "command": {"type": "string", "description": "Socket command, e.g. device_stats"},
         "args": {"type": "object", "description": "Optional args, e.g. {\"id\": \"1\"} (index/PCI/full ID all accepted)"}},
      "required": ["command"]}, t_daemon_query),
    ("fan", "Get/set fan control. set validates speed/curve first, auto-confirms (no 5s revert). Restore with action=auto."
     " NOTE: often unsupported on NVIDIA proprietary driver (no hwmon) — get says so.",
     {"type": "object", "properties": {
         "gpu_id": {"type": "string", "description": "REQUIRED for set/auto when several GPUs (no silent default). Index, PCI or full ID."},
         "action": {"type": "string", "enum": ["get", "set", "auto"], "default": "get"},
         "mode": {"type": "string", "enum": ["curve", "static"], "default": "curve"},
         "speed": {"type": "number", "description": "Static speed 0..1 (or 0..100%). Required for mode=static."},
         "curve": {"type": "object", "description": "E.g. {\"40\":0.35,\"60\":0.6,\"80\":1.0} (2..8 pts, above HW minimum). Required for mode=curve."}},
      "required": ["action"]}, t_fan),
    ("clocks", "Get/set clocks, offsets, voltage boost, performance level. NVIDIA path = gpu_offset/mem_offset (undervolting is driver-locked: use offsets + power cap)."
     " Offsets validated against hardware min/max, applied to all pstates, auto-confirmed. Restore with action=reset.",
     {"type": "object", "properties": {
         "gpu_id": {"type": "string", "description": "REQUIRED for set/reset when several GPUs (no silent default). Index, PCI or full ID."},
         "max_core_clock": {"type": "integer", "description": "MHz"},
         "min_core_clock": {"type": "integer", "description": "MHz"},
         "max_mem_clock": {"type": "integer", "description": "Alias for max_memory_clock (MHz)"},
         "max_memory_clock": {"type": "integer", "description": "MHz"},
         "min_memory_clock": {"type": "integer", "description": "MHz"},
         "gpu_offset": {"type": "integer", "description": "Core offset MHz on ALL pstates (the NVIDIA OC path), e.g. 150"},
         "mem_offset": {"type": "integer", "description": "VRAM offset MHz on ALL pstates (the NVIDIA OC path), e.g. 500"},
         "voltage_boost": {"type": "integer", "description": "NVIDIA boost % (0..100)"},
         "performance_level": {"type": "string", "enum": ["auto", "low", "high", "manual"]}},
      "required": ["action"]}, t_clocks),
]
BY_NAME = {n: f for n, _, _, f in TOOLS}


def tools_list():
    return [{"name": n, "description": d, "inputSchema": s}
            for n, d, s, _ in TOOLS]


def handle(req):
    m = req.get("method")
    rid = req.get("id")
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lact-mcp", "version": VERSION}}}
    if m in ("notifications/initialized", "notifications/cancelled"):
        return None
    if m == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"tools": tools_list()}}
    if m == "tools/call":
        p = req.get("params", {})
        fn = BY_NAME.get(p.get("name"))
        if not fn:
            return {"jsonrpc": "2.0", "id": rid, "result":
                    err(f"unknown tool {p.get('name')!r}")}
        try:
            return {"jsonrpc": "2.0", "id": rid,
                    "result": ok(fn(p.get("arguments") or {}))}
        except (ValueError, RuntimeError, KeyError, TypeError) as e:
            return {"jsonrpc": "2.0", "id": rid, "result": err(str(e))}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"unknown method {m!r}"}}


def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        try:
            resp = handle(req)
        except Exception as e:  # never kill the loop on bad input
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def self_test():
    """Minimal live check: fails loudly if lactd/CLI broken. Ponytail: one check."""
    assert len(TOOLS) == 10, "tool registry changed, update README"
    out = t_list_gpus({})
    assert ":" in out, f"list_gpus unexpected: {out!r}"
    assert "MHz" in t_gpu_stats({"gpu_id": "0"}), "stats missing clocks"
    assert "mode:" in t_fan({"action": "get", "gpu_id": "0"}), "fan get failed"
    assert "ranges:" in t_clocks({"action": "get", "gpu_id": "0"}), "clocks get failed"
    r = sock_query("list_devices")
    assert r["status"] == "ok" and r["data"], "socket list_devices failed"
    n = len(r["data"])
    # power cap may not exist (e.g. iGPU): last GPU usually has one, else skip
    if n > 1:
        assert "W" in t_power({"action": "get", "gpu_id": "1"}), "power get failed"
    print(f"lact-mcp {VERSION} self-test OK ({n} gpu(s))")


if __name__ == "__main__":
    if "--test" in sys.argv:
        self_test()
    elif "--version" in sys.argv:
        print(f"lact-mcp {VERSION}")
    else:
        serve()
