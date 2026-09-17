#!/usr/bin/env python3
"""MCP server (stdlib only, no deps) to control GPUs via LACT on Linux.

Transport: JSON-RPC 2.0 over stdio, one JSON object per line (MCP stdio).
Backend: `lact cli` subprocess + JSON socket /run/lactd.sock for advanced calls.

Tools (8): list_gpus, gpu_info, gpu_stats, power, profiles, auto_switch,
           gpu_config_get, daemon_query.
"""
import json
import os
import re
import socket
import subprocess
import sys

VERSION = "0.1.0"
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
    return with_gpu(a.get("gpu_id", "0"), "info")


def t_gpu_stats(a):
    return with_gpu(a.get("gpu_id", "0"), "stats")


def t_power(a):
    gid = a.get("gpu_id", "0")
    action = a.get("action", "get")
    if action == "get":
        return with_gpu(gid, "power-limit", "get")
    if action == "set":
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


def t_gpu_config_get(a):
    gid = a.get("gpu_id", "0")
    # resolve index -> full id via list_devices when short id given
    if ":" not in str(gid):
        devs = sock_query("list_devices")
        try:
            gid = devs["data"][int(gid)]["id"]
        except (IndexError, ValueError, KeyError, TypeError):
            raise ValueError(f"unknown gpu index {gid!r}")
    r = sock_query("get_gpu_config", {"id": gid})
    if r.get("status") != "ok":
        raise RuntimeError(json.dumps(r))
    if r.get("data") is None:
        return f"{gid}: default config (no overrides)"
    return json.dumps(r["data"], indent=2)


def t_daemon_query(a):
    if not a.get("command"):
        raise ValueError("missing 'command' (e.g. device_stats, list_devices)")
    r = sock_query(a["command"], a.get("args"))
    if r.get("status") != "ok":
        raise RuntimeError(json.dumps(r)[:300])
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
         "gpu_id": {"type": "string", "default": "0"},
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
     " device_clocks_info, set_power_cap, set_fan_control). Advanced: write ops apply immediately.",
     {"type": "object", "properties": {
         "command": {"type": "string", "description": "Socket command, e.g. device_stats"},
         "args": {"type": "object", "description": "Optional args, e.g. {\"id\": \"<pci-id>\"}"}},
      "required": ["command"]}, t_daemon_query),
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
        except (ValueError, RuntimeError) as e:
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
    assert len(TOOLS) == 8, "tool registry changed, update README"
    out = t_list_gpus({})
    assert ":" in out, f"list_gpus unexpected: {out!r}"
    assert "MHz" in t_gpu_stats({"gpu_id": "0"}), "stats missing clocks"
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
