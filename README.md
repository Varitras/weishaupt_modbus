# Weishaupt Modbus

A Home Assistant integration that reads and controls a Weishaupt heat pump
(WBB, WWP, WSB and related models) over Modbus TCP: temperatures, operating
states, error codes, energy statistics and an estimated heat output, plus the
setpoints and operating modes the controller lets you write. An experimental
second entry reads the controller's local web interface for values Modbus does
not carry.

**The documentation is in the [wiki](https://github.com/Varitras/weishaupt_modbus/wiki)**:
setup, every entity, how data updates, the web interface, troubleshooting,
migration and the Modbus register reference.

This is a fork of [OStrama/weishaupt_modbus](https://github.com/OStrama/weishaupt_modbus).
It differs from upstream on purpose:

- **Modbus first.** Upstream's web-interface scraping is removed, together
  with its settings and entities; an entry that still carries them is cleaned
  up on first start. A new, separate and deliberately gentle reader of the
  web interface is experimental (see [Web interface (experimental)](#web-interface-experimental)).
- **One connection to the controller, shared.** Since 2.0 the integration
  borrows its Modbus unit from Home Assistant's own `modbus` integration
  instead of opening a socket of its own.
- **Entity ids are yours.** The integration no longer rewrites entity ids on
  every start; a rename in Home Assistant stays.
- **Two Home Assistant versions are tested** on every change: the declared
  minimum (`hacs.json`) and the newest final release.
- **A test suite guards the register table**: every register has a name in
  every language, every status value has a state text, no translation
  outlives its item.

## Supported devices

Weishaupt heat pumps whose controller offers *Modbus TCP* in its own settings
menu (see [Prerequisites](#prerequisites)). It is developed and checked on a
WBB. Power maps for the estimated heat output ship for models of the WAB, WBB,
WSB and WWP LS series; a model without one works too. For a model that does
not work as expected, a register scan helps add it (see
[Troubleshooting](https://github.com/Varitras/weishaupt_modbus/wiki/Troubleshooting-and-Diagnostics)).

It has not been tested with heat pumps that use the separate Weishaupt Modbus
module (WEM-Modbus gateway); [Weishaupt_CanApiJson](https://github.com/BorgNumberOne/Weishaupt_CanApiJson/)
may be what you are looking for.

## Prerequisites

Modbus TCP has to be enabled on the heat pump:
User → Settings → Modbus TCP

- **Parameter**: On
- **Network**: either the address of your Home Assistant host (only that host
  may connect) or your network address, e.g. `192.168.178.0`, to allow every
  host in it. The first is the safer choice.
- **Netmask**: the netmask of your network, usually `255.255.255.0`.
- **Port** 502, **slave address** 1.

Modbus TCP is unencrypted, and every host the network setting admits can read
and write; Weishaupt asks for a direct connection between client and heat pump
rather than a home network. At least keep the heat pump on a trusted local
network and never expose port 502 to the internet.

## Installation

**Minimum Home Assistant version: 2026.9.** Version 2.0 builds on the shared
Modbus connection that arrived with 2026.9; older releases cannot load it.

Add this repository as a custom repository in HACS (category *Integration*)
and install it, or copy `custom_components/weishaupt_modbus/` into the
`custom_components/` directory of your Home Assistant configuration and
restart. Then add the integration under *Settings → Devices & services →
Add integration → Weishaupt Modbus*, or directly:

[![Start Config Flow](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=weishaupt_modbus)

The setup dialog checks that the heat pump answers before it creates the
entry. Add one entry per heat pump. The setup parameters, *Reconfigure*, the
options and removal are described under
[Installation and Setup](https://github.com/Varitras/weishaupt_modbus/wiki/Installation-and-Setup);
prefix and device postfix cannot be changed after setup.

Coming from 1.x or an earlier 2.x release: see
[Migration](https://github.com/Varitras/weishaupt_modbus/wiki/Migration).

## Entities

Each entry creates the devices System, Heat pump, Hot water, Heating circuit
(1 - 5), 2nd heat source, Statistics and Input/output, each named with a `WH`
prefix and the device postfix. The entity types are sensors, numbers
(writable setpoints), selects (writable modes) and switches (setpoints the
controller can switch off). Every entity, with its register, unit and range, is
listed under [Entities](https://github.com/Varitras/weishaupt_modbus/wiki/Entities).

The integration registers no actions of its own. Values are written with Home
Assistant's standard actions: `number.set_value`, `select.select_option`,
`switch.turn_on` and `switch.turn_off`. Writes go to the heat pump's EEPROM,
which Weishaupt rates for 100 000 writes; trigger automations that set values
on a change, not on a schedule. Two sensors count the writes, and the options
set a warning threshold and a daily limit (see
[How Data Updates](https://github.com/Varitras/weishaupt_modbus/wiki/How-Data-Updates)).

Show the power map of your model on a dashboard:

```yaml
type: picture
image: /local/local/weishaupt_modbus_powermap.svg
```

## Web interface (experimental)

The controller's local web interface, *WEM Lokal*, shows values Modbus does
not carry: the refrigerant circuit, compressor speed and counters, target and
actual output, the flow rate, energy statistics to three decimals including
the yearly totals, and the heating power limit. The integration reads them in
a second entry beside the heat pump and sets one of them, the heating power
limit; nothing else is written.

The controller's web server is slow and now and then answers with an
incomplete page, so the integration asks little: one request at a time, at
least 5 seconds apart, never at the same moment as its Modbus requests. After
three failures in a row it pauses, asks that page again after 15 minutes (or
the shortest page interval, if longer), then at waits doubling up to 2 hours,
and resumes by itself when the page comes whole. Switching the web server on,
adding the entry and everything it reads are described under
[Web Interface](https://github.com/Varitras/weishaupt_modbus/wiki/Web-Interface).
It needs the web interface's language set to Auto, German or English.

For another language or another model, open an
[issue](https://github.com/Varitras/weishaupt_modbus/issues) and attach the
file [`tools/webif_capture.py`](tools/webif_capture.py) writes: it collects the
menu and value titles the integration needs. On Windows,
`tools\webif_capture.bat` runs it without an installed Python. How to run it
and what the file holds:
[Capturing the titles](https://github.com/Varitras/weishaupt_modbus/wiki/Web-Interface#capturing-the-titles).

## Known limitations

- The yearly energy registers answer but stay at 0 on every controller seen
  so far; the yearly total exists only on the display, in the WEM portal and in
  the web interface.
- The performance factors over Modbus are shown only from 5 kWh of
  electricity on, since both energies count whole kWh.
- The heat output is calculated from the power map, not measured. The
  electrical power is measured by the heat pump, but undocumented and checked
  on one model only.
- The web interface's values are those of a WBB; another model may show other
  rows, and the dialog then says it is not supported yet.

More under [How Data Updates](https://github.com/Varitras/weishaupt_modbus/wiki/How-Data-Updates#known-limits-of-the-values).

## Troubleshooting

For a problem, attach a debug log and the diagnostics to an
[issue](https://github.com/Varitras/weishaupt_modbus/issues): on the
integration's page choose *Enable debug logging*, reproduce the problem, then
*Disable debug logging*; *Download diagnostics* is in the entry's menu. The
diagnostics leave out the heat pump's address and the names you typed; the log
does not, so replace addresses, host names and names of people or rooms before
you attach it. For a heat pump model nobody has tested,
[`tools/weishaupt_scan.bat`](tools/weishaupt_scan.bat) reads every register it
answers into a CSV file, read only. Error messages, repair notices and what the
log shows are explained under
[Troubleshooting and Diagnostics](https://github.com/Varitras/weishaupt_modbus/wiki/Troubleshooting-and-Diagnostics).

## Development

Tests need Linux (Home Assistant does not run natively on Windows; WSL2 is
fine). Create a virtual environment with the test plugin and the runtime
dependencies:

```sh
pip install -r requirements_test.txt   # test plugin, ruff, mypy, pip-audit
pytest tests/ -q            # the fast everyday run
pytest tests/ -q -m ""      # everything, including the end-to-end tests
```

Besides Python, the suite needs two programs on the `PATH`: PowerShell 7
(`pwsh`) for the tests of the Windows tools, and `gitleaks` 8.30 or newer for
the secret-scan tests and `check.sh` - distribution packages are older, so
put a current release first in the `PATH`.

Before pushing, run the gates of the test workflow:

```sh
PYTHON=/path/to/venv/bin/python MIN_HA_PYTHON=/path/to/min-ha-venv/bin/python .github/scripts/check.sh
```

Without `MIN_HA_PYTHON` the run against the minimum Home Assistant is
skipped, and says so. hassfest, the HACS validation and CodeQL run only in
CI.

`tests/README.md` explains the guards, the budgets and the mutation run, and
what to do when one of them turns red. Tests never touch a real controller:
the `mock_modbus` fixture replaces the connection Home Assistant's `modbus`
integration hands out with the library's in-memory unit.

## Disclaimer

The developers of this integration are not affiliated with Weishaupt. It was
created in their spare time from publicly available information. Use is at
your own risk; the developers are not liable for damage arising from it.
