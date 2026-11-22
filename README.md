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
- **Home Assistant compatibility tests** run on the declared minimum
  (`hacs.json`) and the newest stable version supported by the test framework.
- **A test suite guards the register table**: every register has a name in
  every language, every status value has a state text, no translation
  outlives its item.

## Supported devices

Weishaupt heat pumps whose controller offers *Modbus TCP* in its own settings
menu (see [Prerequisites](#prerequisites)). It is developed and checked on a
WBB. Power maps for the estimated heat output ship for models of the WAB, WBB,
WEB (Evoblock), WSB and WWP LS series; a model without one works too. For a
model that does not work as expected, a register scan helps add it (see
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
`switch.turn_on` and `switch.turn_off`. Weishaupt rates the heat pump's EEPROM
for 100 000 writes. Trigger automations that change stored settings only when
the value changes. The PV power setpoint (register 40002) is held in RAM: its
writes are not counted and do not use up the daily limit. Two sensors show
the counts of the other writes. The options set the warning threshold and
daily limit (see
[How Data Updates](https://github.com/Varitras/weishaupt_modbus/wiki/How-Data-Updates)).

Show the power map of your model on a dashboard:

```yaml
type: picture
image: /local/local/weishaupt_modbus_powermap.svg
```

For a further heat pump with a device postfix, the file name ends in
`_<postfix>`, such as `weishaupt_modbus_powermap_2.svg`.

## Web interface (experimental)

The controller's local web interface, *WEM Lokal*, shows values Modbus does
not carry: the refrigerant circuit, compressor speed and counters, target and
actual output, the flow rate, energy statistics to three decimals including
the yearly totals, and the heating power limit. The integration reads them in
a second entry beside the heat pump and sets the heating power limit, on an
Evoblock each heating circuit's minimum flow temperature, and, once switched
on, the hot water modulation; nothing else is written.

On an Evoblock, the entry also reads two pages Info lists there, when it
finds them: the lowest-numbered heating circuit's, for the outdoor
temperature averaged as the heating curve follows it (*AT Mittelwert*,
*AT Langzeitwert*), and the 2nd heat generator's, for the switching cycles
of each heating rod stage (*Schaltspiele E1*, *E2*). It also reads the
settings of each circuit Info lists, *Heizkreis n › Einstellungen*: the
minimum flow temperature, as a number to set, and the maximum, the demand
increase and the SG Ready raise; and *Warmwasser › Einstellungen*: the hot
water's system operating mode. One of these pages that comes half, or with a
value the integration reads blank or twice, also when searched again, is left
out rather than failing the dialog; so are two circuits the overview links to
one address.
Each of the two pages Info lists has an interval of its own, and the settings
pages share one; all three are 15 minutes
unless set otherwise. An entry added before this version reads them after
**Reconfigure**.

On a WBB and an Evoblock, *Wärmepumpe › Modulation* sets the heat pump's
output while the hot water loads: Automatik, or a fixed 50 to 100 %. It is
off unless switched on in the web interface entry's options, as it costs a
page more; then it is read once an hour and offered as a choice to set. An
entry added before this version shows the switch after **Reconfigure** once,
where the controller offers the page.

The controller's web server is slow and now and then answers with an
incomplete page, so the integration asks little: one request at a time, at
least 5 seconds apart, never at the same moment as its Modbus requests. After
three failures in a row it pauses, asks that page again after 15 minutes (or
the shortest page interval, if longer), then at waits doubling up to 2 hours,
and resumes by itself when the page comes whole. The controller may change
its page addresses; the integration then finds its pages again by itself, at
most once per update. A page it reads that it cannot find again, or cannot
tell apart from another one, needs **Reconfigure**, and a notice says so; the
modulation, while switched off, is simply dropped. Switching the web
server on, adding the entry and everything it reads are described under
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
  the web interface. They and the performance factor of the year are created
  disabled.
- The H1 inputs show off/on: every function the controller offers for them is
  a contact. The web interface counts them one higher (its H1.3 is *Input
  H1.2*). They used to be temperatures, so Home Assistant lists their old
  statistics under Developer tools › Statistics, where they can be deleted.
- The performance factors over Modbus are shown only from 5 kWh of
  electricity on, since both energies count whole kWh.
- The heat output is calculated from the power map, not measured. At part
  load it reads too low where the map gives only the maximum output: a
  modulating pump delivers more than its share of it. Where the web interface is set up,
  its *Ist Leistung* is the value to use (on an Evoblock also
  *Leistungsabgabe*, flow rate × spread). The electrical power is measured by
  the heat pump, but undocumented and checked on one model only.
- The power map of the WEB 13/20 also carries minimum curves, and for it the
  heat output is minimum + request × (maximum − minimum), 0 W at 0 %. The
  curves were read from the diagram in the datasheet (Weishaupt Fachinformation
  83603813), with W75 held at its −7 °C value below that, where the heat pump
  does not reach 75 °C; the formula was checked on one WEB 13/20 only, against
  flow rate × spread over a week (+6 % instead of −22 %). On a WBB the same
  formula read 40 to 60 % high at low request, so every other map keeps the
  request × maximum.
- The web interface's values are those of a WBB and, with its titles in German,
  of an Evoblock (seen on a WEB 13/20); another model may show other rows, and
  the dialog then says it is not supported yet.

More on the energies and performance factors under [How Data Updates](https://github.com/Varitras/weishaupt_modbus/wiki/How-Data-Updates#known-limits-of-the-values).

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
