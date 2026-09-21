# lact-mcp — v0.5.0

MCP server (Python stdlib only, **zero dependency**) to control GPUs via [LACT](https://github.com/ilya-zlobintsev/LACT) on Linux — so an AI agent can read stats and manage power / profiles / fan / clocks.

## About

`lact-mcp` exposes the LACT daemon (socket + CLI) as MCP tools over stdio:
monitoring first, safe writes second. Every write validates against hardware
limits *before* touching the daemon, auto-confirms (lactd reverts
unconfirmed changes after ~5 s), and tells how to restore stock settings.
See [CHANGELOG.md](CHANGELOG.md) for the history.

## Requirements

- Linux + LACT installed, `lactd` running (`sudo systemctl enable --now lactd`)
- User in the lact admin group (`wheel` by default): `groups | grep wheel`
- Python 3.10+ (stdlib only)

## Run

```bash
python3 lact_mcp.py --test    # live self-check (needs lactd)
python3 lact_mcp.py           # MCP stdio server
```

## Use with opencode (global MCP, no prompt)

Add to `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "mcp": {
    "lact": {
      "type": "local",
      "command": ["python3", "/path/to/lact-mcp/lact_mcp.py"]
    }
  }
}
```

Then quit and relaunch opencode.

## Tools (11)

| Tool | What |
|---|---|
| `list_gpus` | List GPUs (index, PCI ID, model) |
| `gpu_info` | Static info: driver, VRAM, VBIOS, PCIe |
| `gpu_stats` | Live: clocks, voltage, power, temps, VRAM, fans, throttling |
| `power` | `get` / `set` power cap (`set` validates the configurable range first) |
| `profiles` | `list` / `get` / `set` / `create` (clone via `from`) / `delete` (refuses current) / `import` (PenguinBurner auto-uv JSON, `apply` to set current) |
| `auto_switch` | `get` / `enable` / `disable` auto profile switching |
| `gpu_config_get` | Full applied config (fan curve, clocks…) via lactd socket |
| `daemon_query` | Raw lactd socket call (`device_stats`, `set_power_cap`, `set_fan_control`…) |
| `fan` | `get` state / `set` static speed or curve (HW-minimum validated) / `auto` restore |
| `clocks` | `get` ranges+offsets / `set` min-max clocks, pstate offsets, boost, perf level / `reset` |
| `voltage` | `get` boost+VF curve / `set` per-point VF offsets, boost (NVIDIA) or offset/min/max (AMD) / `reset` |

`gpu_id` accepts the short index (`"1"`, or the number `1`), the PCI address
(`"0000:01:00.0"`) or the full LACT ID — everywhere, including `daemon_query`
args.
Reads default to GPU `0`; **writes refuse a default when several GPUs exist**
(pass `gpu_id` explicitly, see `list_gpus`).

Examples:

```jsonc
// Fix fans at 50% on the RTX 3080, then back to automatic
{ "name": "fan", "arguments": { "gpu_id": "1", "action": "set", "mode": "static", "speed": 0.5 } }
{ "name": "fan", "arguments": { "gpu_id": "1", "action": "auto" } }
// +150 MHz core / +500 MHz VRAM on every pstate, then stock clocks
{ "name": "clocks", "arguments": { "gpu_id": "1", "action": "set", "gpu_offset": 150, "mem_offset": 500 } }
{ "name": "clocks", "arguments": { "gpu_id": "1", "action": "reset" } }
// Undervolt-style: lower VF point 0 to +25 MHz offset, then restore
{ "name": "voltage", "arguments": { "gpu_id": "1", "action": "set", "vf_points": { "0": 25 } } }
{ "name": "voltage", "arguments": { "gpu_id": "1", "action": "reset" } }
```

## NVIDIA notes (proprietary driver)

- Fan control is usually **unsupported** (no hwmon) — `fan get` says so.
- Direct voltage is **driver-locked** — use `voltage` VF per-point offsets
  (the undervolt path) + `power` cap. `set_clocks_value` VF commands are
  AMD-only and silently ignored on NVIDIA.
- Daemon errors are returned in full (including the valid-command list).

## Related

- [LACT](https://github.com/ilya-zlobintsev/LACT) — the GPU controller this
  server drives. `lact-mcp` is an independent third-party MCP bridge (not
  affiliated); every write maps to a documented LACT socket/CLI call.

## Safety

- `power` / `fan` / `clocks` `set` refuse values outside hardware limits first.
- Socket writes auto-confirm (no silent 5 s revert); every reply says how to restore.
- `daemon_query` GPU-config writes auto-confirm like the rest: every `set_*`
  command (incl. `set_gpu_config`) and `batch_set_clocks_value` (no silent
  5 s revert).
- No secrets, no network: local socket + `lact cli` only.

## Skipped (add when needed)

- `snapshot` tool — use `daemon_query` or `lact cli snapshot` directly.
- Remote TCP daemon — disabled by default in LACT (no auth); not exposed.
