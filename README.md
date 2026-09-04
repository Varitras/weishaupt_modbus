# Weishaupt Modbus

A Home Assistant integration that reads and controls a Weishaupt heat pump
(WBB, WWP, WSB and related models) over Modbus TCP.

This is a fork of [OStrama/weishaupt_modbus](https://github.com/OStrama/weishaupt_modbus).
It differs from upstream on purpose:

- **Modbus only.** The experimental web-interface scraping is removed, together
  with its settings and entities. An entry that still carries web-interface
  settings is cleaned up on first start.
- **One connection to the controller, shared.** Since 2.0 the integration
  borrows its Modbus unit from Home Assistant's own `modbus` integration
  instead of opening a socket of its own (see *What changed in 2.0*).
- **Entity ids are yours.** The integration no longer rewrites entity ids on
  every start; a rename in Home Assistant stays.
- **Two Home Assistant versions are tested** on every change: the declared
  minimum (`hacs.json`) and the newest final release.
- **A test suite guards the register table**: every register has a name in
  every language, every status value has a state text, no translation
  outlives its item.

If your heat pump has the separate Weishaupt Modbus module, this integration
will not work. [Weishaupt_CanApiJson](https://github.com/BorgNumberOne/Weishaupt_CanApiJson/)
may be what you are looking for.

## Installation

Add this repository as a custom repository in HACS (category *Integration*)
and install it, or copy `custom_components/weishaupt_modbus/` into the
`custom_components/` directory of your Home Assistant configuration and
restart. Then add the integration:

[![Start Config Flow](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=weishaupt_modbus)

**Minimum Home Assistant version: 2026.9.** Version 2.0 builds on the shared
Modbus connection that arrived with 2026.9; older releases cannot load it.

## What changed in 2.0

The Weishaupt controller accepts a single Modbus TCP connection. Up to 1.x the
integration opened that connection itself, with its own reconnect logic and
block planner. Since 2.0 it asks Home Assistant's `modbus` integration for a
*unit* on the connection to the controller's address. Home Assistant keeps one
connection per endpoint and serialises everything that goes over it, so two
entries of this integration - or another integration asking for the same
endpoint - queue up behind one link instead of fighting over it. A hub from
the YAML `modbus:` configuration is *not* part of that: it opens a client of
its own, so pointing one at the same controller still costs the second
connection the controller does not have. The wire is handled by the
[modbus-connection](https://github.com/home-assistant-libs/modbus-connection)
library (tmodbus backend), which Home Assistant installs with its `modbus`
integration.

What stays the same: the entities, their unique ids and history, the entity
ids you chose, the options (poll interval, EEPROM write warning and limit), the
power map and the write counters. What is gone: this integration's own
`pymodbus` requirement, its reconnect and back-off logic, and the
five-register block limit - the controller serves each address band in one
read.

Upgrading: install 2.0 and restart. Existing entries migrate on first start
(entry version 11); an entry keeps the host and port you configured. If the
controller does not answer during setup, Home Assistant retries.

## Configuration

The only mandatory parameter is the address of your heat pump. The port is
502 unless you changed it on the heat pump.

- **Prefix** is part of every entity's unique id. Leave it alone unless you
  are migrating from another integration and want to keep the recorded history.
- **Device postfix** lets you add more than one heat pump. Leave it empty for
  a single pump; every further pump needs a postfix of its own, the flow
  refuses an empty or reused one.

Prefix and device postfix are part of every entity's unique id and cannot
be changed afterwards: a change would orphan the recorded history of every
entity and start the EEPROM write counters over. To rename, remove the
entry and add it again.
- **Heizkreis 2–5** enable the entities of additional heating circuits.
- **Kennfeld file** selects the power map for your model (see below).

The **poll interval** (default 30 s) is an option, not part of the setup:
open the integration's *Options* dialog to change it. A change reloads the
integration.

### The power map

The heat power (*Wärmeleistung*) is calculated from the power request,
the outside temperature and the flow temperature. That relation is model
specific; the integration ships a precompiled grid per model, read from the
graphs in the Weishaupt documentation:

- `weishaupt._wab8_kennfeld.json`
- `weishaupt_ls13_kennfeld.json`
- `weishaupt_wab11_kennfeld.json`
- `weishaupt_wbb12_kennfeld.json`
- `weishaupt_wbb20_kennfeld.json`
- `weishaupt_wbb_kennfeld.json`
- `weishaupt_wsb12_kennfeld.json`
- `weishaupt_wsb15_armd_kennfeld.json`
- `weishaupt_wsb6_kennfeld.json`
- `weishaupt_wsb8_armea_kennfeld.json`
- `weishaupt_wsb8_kennfeld.json`
- `weishaupt_wwp_ls_10_kennfeld.json`
- `weishaupt_wwp_ls_8_kennfeld.json`

Known gap: the two `wwp_ls` grids carry 0 W at -20 °C on the 55 °C flow curve,
as read from the source graphs. The heat power is understated on very cold days
for those models until someone with the documentation supplies the point.

Pick the file matching your model. If yours is missing, copy the closest
one, adjust `known_x`, `known_y` and `known_t` from your documentation and
compile it once with `.github/scripts/compile_kennfeld.py` (needs `numpy`;
`scipy` gives a smoother curve). The integration only reads compiled grids;
it draws the preview picture itself. Contributions of new grids are welcome.

## Setting up the heat pump

Modbus TCP has to be enabled on the heat pump:
User → Settings (second page) → Modbus TCP

- **Parameter**: On
- **Network**: either the address of your Home Assistant host (only that host
  may connect) or your network address, e.g. `192.168.178.0`, to allow every
  host in it. The first is the safer choice.
- **Netmask**: the netmask of your network, usually `255.255.255.0`.
- **Port** 502, **slave address** 1.

Modbus TCP has no authentication and no encryption. Keep the heat pump on a
trusted local network and never expose port 502 to the internet.

Writes go to the heat pump's EEPROM, which Weishaupt rates for 100 000 writes
over its lifetime. The integration never writes a value that is already set;
keep automations that set values on a change, not on a schedule. Two
diagnostic sensors on the system device count the writes that actually went
out, in total and today. The *Options* dialog holds a warning threshold
(default 50 writes a day, logged once when reached) and a daily limit
(default off) beyond which writes are refused until the next day.

## Development

Tests need Linux (Home Assistant does not run natively on Windows; WSL2 is
fine). Create a virtual environment with the test plugin and the runtime
dependencies:

```sh
pip install -r requirements_test.txt   # test plugin, ruff, mypy, pip-audit
pytest tests/ -q            # the fast everyday run
pytest tests/ -q -m ""      # everything, including the end-to-end tests
```

Before pushing, run every gate the CI runs:

```sh
PYTHON=/path/to/venv/bin/python .github/scripts/check.sh
```

`tests/README.md` explains the guards, the budgets and the mutation run, and
what to do when one of them turns red. Tests never touch a real controller:
the `mock_modbus` fixture replaces the connection Home Assistant's `modbus`
integration hands out with the library's in-memory unit.

## Disclaimer

The developers of this integration are not affiliated with Weishaupt. It was
created in their spare time from publicly available information. Use is at
your own risk; the developers are not liable for damage arising from it.
