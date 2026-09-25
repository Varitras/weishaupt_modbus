# Weishaupt Modbus

A Home Assistant integration that reads and controls a Weishaupt heat pump
(WBB, WWP, WSB and related models) over Modbus TCP: temperatures, operating
states, error codes, energy statistics and an estimated heat output, plus the
setpoints and operating modes the controller lets you write.

This is a fork of [OStrama/weishaupt_modbus](https://github.com/OStrama/weishaupt_modbus).
It differs from upstream on purpose:

- **Modbus only.** The experimental web-interface scraping is removed, together
  with its settings and entities. An entry that still carries web-interface
  settings is cleaned up on first start.
- **One connection to the controller, shared.** Since 2.0 the integration
  borrows its Modbus unit from Home Assistant's own `modbus` integration
  instead of opening a socket of its own (see [Upgrading from 1.x](#upgrading-from-1x)).
- **Entity ids are yours.** The integration no longer rewrites entity ids on
  every start; a rename in Home Assistant stays.
- **Two Home Assistant versions are tested** on every change: the declared
  minimum (`hacs.json`) and the newest final release.
- **A test suite guards the register table**: every register has a name in
  every language, every status value has a state text, no translation
  outlives its item.

## Supported devices

Weishaupt heat pumps whose controller offers *Modbus TCP* in its own settings
menu (see [Prerequisites](#prerequisites)). Power maps ship for models of
the WAB, WBB, WSB and WWP LS series (see [The power map](#the-power-map));
a model without one works too, only the estimated heat output needs a map.

If your heat pump has the separate Weishaupt Modbus module, this integration
will not work. [Weishaupt_CanApiJson](https://github.com/BorgNumberOne/Weishaupt_CanApiJson/)
may be what you are looking for.

## Prerequisites

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

## Installation

**Minimum Home Assistant version: 2026.9.** Version 2.0 builds on the shared
Modbus connection that arrived with 2026.9; older releases cannot load it.

Add this repository as a custom repository in HACS (category *Integration*)
and install it, or copy `custom_components/weishaupt_modbus/` into the
`custom_components/` directory of your Home Assistant configuration and
restart. Then add the integration under *Settings → Devices & services →
Add integration → Weishaupt WBB*, or directly:

[![Start Config Flow](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=weishaupt_modbus)

The setup dialog checks that the heat pump answers before it creates the
entry. Add one entry per heat pump.

## Configuration

### Setup parameters

| Parameter | Default | Meaning |
|---|---|---|
| Host | - | Name or IP address of the heat pump. Mandatory. |
| Port | 502 | Modbus TCP port, as set on the heat pump. |
| Prefix | `weishaupt_wbb` | Part of every entity's unique id. Leave it alone unless you are migrating from another integration and want to keep the recorded history. |
| Device postfix | empty | Tells several heat pumps apart. Leave it empty for a single pump; every further pump needs a postfix of its own, the dialog refuses an empty or reused one. The postfix is appended to device names and ids. |
| Operation map file | `weishaupt_wbb_kennfeld.json` | The power map of your model (see [The power map](#the-power-map)). |
| 2nd - 5th heating circuit | off | Adds the entities of the additional heating circuit. |
| Name device prefix | off | Puts the prefix in front of every entity name, e.g. `weishaupt_wbb_Outside temperature`. |
| Name topic prefix | off | Puts the device's short name in front of every entity name, e.g. `WP_` for the heat pump, `WW_` for hot water. |

Prefix and device postfix are part of every entity's unique id and cannot
be changed afterwards: a change would orphan the recorded history of every
entity and start the EEPROM write counters over. To change them, remove the
entry and add it again.

### Reconfigure

*Reconfigure* in the entry's menu changes host, port, power map, heating
circuits and the two name options without losing history. The heat pump has
to answer at the new address before the change is saved.

### Options

The *Options* dialog holds the settings that can change at any time. A
change reloads the integration.

| Option | Default | Range | Meaning |
|---|---|---|---|
| Poll interval | 30 s | 5 - 600 s | How often all registers are read. |
| Warn at this many writes per day | 50 | 0 - 100 000 | Logs a warning once when reached; 0 never warns. |
| Refuse writes above this many per day | 0 (no limit) | 0 - 100 000 | Writes beyond it are refused until the next day. Must not be below the warning threshold. |

## How data is updated

The integration polls the heat pump at the poll interval and reads every
register of the enabled devices in one pass. It asks Home Assistant's
`modbus` integration for the connection, so every entry and every other
integration using the same address shares one link, which the controller
needs (it accepts a single client).

A poll that fails keeps the last values; the entities go unavailable on the
fourth failed poll in a row and come back with the next good one. A device
that answers but refuses the system registers 30001-30006 is not treated as
a heat pump with missing modules: that poll fails.

A write goes out immediately and only if the value differs from what the
controller holds.

## Entities

Each entry creates these devices, each named with a `WH` prefix and the
device postfix:

| Device | Contains |
|---|---|
| System | System operating mode, PV setpoint, outside and air intake temperature, error, warning and status codes, write counters |
| Heat pump | Operating state, error, power request, flow, return, evaporation and buffer temperatures, spread, estimated heat output, pump and volume-flow settings |
| Hot water | Temperature and setpoint, normal and lowering setpoints, SG-Ready increase, hot water push |
| Heating circuit (1 - 5) | Room temperature, humidity and setpoints, flow temperature and setpoint, operating mode, request type, pause/party, heating curve, summer/winter changeover, constant temperatures |
| 2nd heat source | Status, operating hours and switching cycles of the second heat source and the electric heaters, limit and bivalence temperatures |
| Statistics | Energy today, yesterday, this month and this year - total, heating, hot water, cooling and defrost - and the performance factors |
| Input/output | SG-Ready 1 and 2, inputs H1.2 - H1.5, DE1 and DE2, and their configuration |

The entity types are sensors (read-only values), numbers (writable
setpoints), selects (writable modes) and switches.

**Setpoints that can be switched off.** Some setpoints can be switched off
at the controller - the constant flow temperatures, the summer/winter
switchover and the SG-Ready boost. The register then reports an "off" word
instead of a temperature. Each of these comes as a number *and* a switch
(`… active`): the number reads unknown while the setpoint is off, the switch
turns it off and back on (restoring the value it held before, or the lowest
allowed one).

**Reported setpoints.** The setpoints the controller only reports - room,
flow and DHW setpoint temperature - read unknown while the controller
demands nothing, and say so: their `demand` attribute is `none` then and
`active` while a setpoint is in force. (The controller reports "no demand"
as the value 1, which used to show as 0.1 °C.)

**Write counters.** Writes go to the heat pump's EEPROM, which Weishaupt
rates for 100 000 writes over its lifetime. Two diagnostic sensors on the
System device count the writes that actually went out, in total and today;
the [Options](#options) set a warning threshold and a daily limit.

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

The two `wwp_ls` grids follow the "compressor frequency maximal" curves of the
outdoor-unit manuals (83306301, 83306401). Their 55 °C curve ends at -15 °C,
the operating limit; below it the value is carried on flat rather than
dropping to 0 W, which used to halve the interpolated power for 45 °C flow.

Pick the file matching your model. If yours is missing, copy the closest
one, adjust `known_x`, `known_y` and `known_t` from your documentation and
compile it once with `.github/scripts/compile_kennfeld.py` (needs `numpy`;
`scipy` gives a smoother curve, `pygal` draws the preview picture). The
integration only reads compiled grids. Contributions of new grids are welcome.

The preview picture of the selected map is published as
`www/local/weishaupt_modbus_powermap.svg` (with `_<postfix>` for a further
pump); a picture that carries script or links to the web is refused.

## Actions

The integration registers no actions of its own. Values are written with
Home Assistant's standard actions: `number.set_value`,
`select.select_option`, `switch.turn_on` and `switch.turn_off`.

## Examples

Show the power map of your model on a dashboard:

```yaml
type: picture
image: /local/weishaupt_modbus_powermap.svg
```

When an automation sets a value, trigger it on a change (a PV surplus, a
tariff switch), not on a schedule: every write that changes a value costs an
EEPROM write, and the integration already skips writes of a value that is
already set.

## Known limitations

- The controller accepts a single Modbus TCP client. A hub from the YAML
  `modbus:` configuration, or another tool, pointed at the same heat pump
  takes that connection away.
- The yearly energy registers (36104 and the other `… Jahr` rows) answer but
  stay at 0 on every controller seen so far, even after years of operation -
  the yearly total exists only on the display and in the WEM portal. The
  yearly performance factor therefore has no value.
- The heat output is calculated from the power map, not measured.
- Prefix and device postfix cannot be changed after setup.
- Heat pumps with the separate Weishaupt Modbus module are not supported.

## Troubleshooting

**Setup says "Failed to connect".** Check that Modbus TCP is on (see
[Prerequisites](#prerequisites)), that its network and netmask admit the
Home Assistant host, that the port matches, and that no other client holds
the heat pump's single connection.

**All entities are unavailable.** Four polls in a row failed. The heat pump
is off the network, or another client took the connection; the entities come
back with the next good poll.

**A write is refused.** The value is outside the range the controller
currently allows, or the daily write limit from the [Options](#options) is
reached.

**The recorder warns about a changed unit after an upgrade.** See
[Upgrading from 1.x](#upgrading-from-1x).

**Debug logging.** On the integration's page choose *Enable debug logging*,
reproduce the problem, then *Disable debug logging*; the log file is
downloaded. Attach it to an [issue](https://github.com/Varitras/weishaupt_modbus/issues).

## Upgrading from 1.x

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
`pymodbus` requirement, the `pygal` requirement (the preview picture is drawn
by the compile script, not at runtime), its reconnect and back-off logic, and
the five-register block limit - the controller serves each address band in
one read.

New in 2.0: a switch beside every setpoint the controller can switch off
(see [Entities](#entities)). New entities, nothing existing is renamed.

One state string changed: operating status 0 (undefined) used to be
published as `..._pvmode`; it is `..._undefined` now. An automation that
matched on that state needs the new name.

Upgrading: install 2.0 and restart. Existing entries migrate on first start
(entry version 11); an entry keeps the host and port you configured. If the
controller does not answer during setup, Home Assistant retries.

After the upgrade the recorder warns once per sensor whose unit changed:
the three relabelled second-heat-source counters (hours and cycles had
swapped names), the H1.x inputs (now °C, as the data-point list says) and
a few unit-less rows (an empty unit became none). Open *Developer tools ->
Statistics*, and for each listed entity choose to update the unit of the
recorded statistics - the history is the register's and stays.

## Removal

1. *Settings → Devices & services → Weishaupt WBB*, open the entry's menu and
   choose *Delete*. This removes its devices and entities; what the
   recorder already stored stays in its database.
2. Delete `www/local/weishaupt_modbus_powermap*.svg` from your configuration
   directory if you no longer want the preview picture.
3. To remove the integration itself, uninstall it in HACS (or delete
   `custom_components/weishaupt_modbus/`) and restart.

Optionally switch Modbus TCP off on the heat pump again.

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
