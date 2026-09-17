# lact-mcp

MCP server (Python stdlib only, **zero dependency**) to control GPUs via [LACT](https://github.com/ilya-zlobintsev/LACT) on Linux — so an AI agent can read stats and manage power / profiles / fan / clocks.

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

## Tools (8)

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

`gpu_id` accepts the short index (`"0"`, `"1"`) or the full PCI ID. Default `0`.

## Safety

- `power set` refuses values outside the GPU's reported range.
- `daemon_query` write ops apply immediately — the model should confirm before using them.
- No secrets, no network: local socket + `lact cli` only.

## Skipped (add when needed)

- `snapshot` tool — use `daemon_query` or `lact cli snapshot` directly.
- Remote TCP daemon — disabled by default in LACT (no auth); not exposed.
