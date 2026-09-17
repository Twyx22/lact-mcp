# Changelog

Every release has a codename. Codenames are winds.

## v0.2.1 — Bora (2026-09-17)

The cold, precise wind: no more writes to the wrong GPU.

**Fixed**
- Writes (`power`/`fan`/`clocks` set, `fan` auto, `clocks` reset) now **refuse
  a default GPU when several exist** — `gpu_id` must be explicit. (This had
  set `performance_level=high` on the iGPU by mistake.)
- `gpu_id` and `daemon_query` ids accept index (`"1"`), PCI address
  (`"0000:01:00.0"`) and full LACT ID everywhere, via unique suffix match.
- Daemon errors are returned **in full** (including the valid-command list)
  with hints for missing fields — no more truncated `set_f...` mysteries.
- `clocks get` explains itself when the card reports no ranges; `fan get`
  warns when the driver exposes no fan control (NVIDIA proprietary).
- Tool descriptions now document the NVIDIA path (offsets + power cap;
  undervolting is driver-locked) with copy-paste examples.

**Verified live** (RTX 3080, cap 280 W, glmark2 1920x1080 off-screen)
- Stock: **38366** → `gpu_offset` +150 / `mem_offset` +500: **39510 (+3%)**,
  applied purely through the MCP `clocks` tool (10 pstate commands, confirmed).

## v0.2.0 — Sirocco (2026-09-17)

The hot wind that taught lact-mcp to cool things down.

**Added**
- `fan` tool — read live fan state (mode, RPM, PWM, curve); set `static`
  speed or custom `curve`; back to `automatic` with `auto`. Speeds validated
  against the real hardware minimum (`pwm_min`, e.g. 30% on RTX 3080) before
  anything is sent — the daemon's refusal becomes a clear client-side error.
- `clocks` tool — read ranges and live offsets; set min/max core & memory
  clocks, per-pstate core/VRAM offsets (validated against each pstate's
  hardware min/max), voltage boost and performance level; `reset` restores
  stock. Batch writes go through `batch_set_clocks_value` when possible.
- Socket writes now **auto-confirm** (`confirm_pending_config`): without it,
  lactd silently reverts after ~5 s. Every write reply tells how to restore.

**Fixed**
- Single `Error:` prefix, daemon errors surfaced as `isError`, explicit
  `missing 'command'` message (all caught by the 26-request test matrix).

**Verified live** (RTX 3080 + Radeon iGPU, lact 0.10.1)
- Fan static 0.3 applied + read back + restored to automatic; curve
  `{40:0.35, 60:0.6, 80:1.0}` applied + read back + restored.
- Offsets +50/+100 applied to all 5 pstates, read back, reset to 0.
- `performance_level` high → auto. Below-minimum speeds and out-of-range
  offsets refused before touching the daemon. System left stock.

## v0.1.1 (2026-09-17)

Error-handling patch: see “Fixed” in v0.2.0 (shipped together).

## v0.1.0 (2026-09-17)

First flight. 8 tools over stdio (`list_gpus`, `gpu_info`, `gpu_stats`,
`power`, `profiles`, `auto_switch`, `gpu_config_get`, `daemon_query`),
zero dependencies, live-tested under `glmark2` load (26 W → 201 W) with a
150 W cap proven binding (`SW_POWER_CAP`, 149.5 W, clocks 1980 → 1770 MHz).
