# lact-mcp — v0.2.0 “Sirocco”

MCP server (Python stdlib only, **zero dependency**) to control GPUs via [LACT](https://github.com/ilya-zlobintsev/LACT) on Linux — so an AI agent can read stats and manage power / profiles / fan / clocks.

## About

`lact-mcp` exposes the LACT daemon (socket + CLI) as MCP tools over stdio:
monitoring first, safe writes second. Every write validates against hardware
limits *before* touching the daemon, auto-confirms (lactd reverts
unconfirmed changes after ~5 s), and tells how to restore stock settings.
Releases carry wind codenames — see [CHANGELOG.md](CHANGELOG.md).

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
      "command": ["python3", "/home/matthieu/Projects/lact-mcp/lact_mcp.py"]
    }
  }
}
```

Then quit and relaunch opencode.

## Tools (10)

| Tool | What |
|---|---|
| `list_gpus` | List GPUs (index, PCI ID, model) |
| `gpu_info` | Static info: driver, VRAM, VBIOS, PCIe |
| `gpu_stats` | Live: clocks, voltage, power, temps, VRAM, fans, throttling |
| `power` | `get` / `set` power cap (`set` validates the configurable range first) |
| `profiles` | `list` / `get` / `set` LACT profiles |
| `auto_switch` | `get` / `enable` / `disable` auto profile switching |
| `gpu_config_get` | Full applied config (fan curve, clocks…) via lactd socket |
| `daemon_query` | Raw lactd socket call (`device_stats`, `set_power_cap`, `set_fan_control`…) |
| `fan` | `get` state / `set` static speed or curve (HW-minimum validated) / `auto` restore |
| `clocks` | `get` ranges+offsets / `set` min-max clocks, pstate offsets, boost, perf level / `reset` |

`gpu_id` accepts the short index (`"0"`, `"1"`) or the full PCI ID. Default `0`.

Examples:

```jsonc
// Fix fans at 50% on the RTX 3080, then back to automatic
{ "name": "fan", "arguments": { "gpu_id": "1", "action": "set", "mode": "static", "speed": 0.5 } }
{ "name": "fan", "arguments": { "gpu_id": "1", "action": "auto" } }
// +100 MHz core on every pstate, then stock clocks
{ "name": "clocks", "arguments": { "gpu_id": "1", "action": "set", "gpu_offset": 100 } }
{ "name": "clocks", "arguments": { "gpu_id": "1", "action": "reset" } }
```

## Safety

- `power` / `fan` / `clocks` `set` refuse values outside hardware limits first.
- Socket writes auto-confirm (no silent 5 s revert); every reply says how to restore.
- `daemon_query` write ops apply immediately — the model should confirm before using them.
- No secrets, no network: local socket + `lact cli` only.

## Skipped (add when needed)

- `snapshot` tool — use `daemon_query` or `lact cli snapshot` directly.
- Remote TCP daemon — disabled by default in LACT (no auth); not exposed.
