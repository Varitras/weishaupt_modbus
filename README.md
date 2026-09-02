# Weishaupt Modbus

A Home Assistant integration that reads and controls a Weishaupt heat pump
(WBB, WWP, WSB and related models) over Modbus TCP.

This is a fork of [OStrama/weishaupt_modbus](https://github.com/OStrama/weishaupt_modbus).
It differs from upstream on purpose:

- **Modbus only.** The experimental web-interface scraping is removed, together
  with its settings and entities. An entry that still carries web-interface
  settings is cleaned up on first start.
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

Minimum Home Assistant version: see `hacs.json`.

## Configuration

The only mandatory parameter is the address of your heat pump. The port is
502 unless you changed it on the heat pump.

- **Prefix** is part of every entity's unique id. Leave it alone unless you
  are migrating from another integration and want to keep the recorded history.
- **Device postfix** lets you add more than one heat pump. Leave it empty for
  a single pump; give every further pump a short name.
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

Pick the file matching your model. If yours is missing, copy the closest
one, adjust `known_x`, `known_y` and `known_t` from your documentation, drop
the `compiled_grid` key and the integration compiles the grid at first start
(needs `numpy`; `scipy` gives a smoother curve). Contributions of new grids
are welcome.

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
what to do when one of them turns red.

## Disclaimer

The developers of this integration are not affiliated with Weishaupt. It was
created in their spare time from publicly available information. Use is at
your own risk; the developers are not liable for damage arising from it.
