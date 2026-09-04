"""Every register definition is complete.

hpconst.py is the integration's data: ~120 ModbusItems that become entities,
each naming an address, a format, a type and a translation key. Nothing
checked any of it - a key that has no entry in strings.json shows up in the
UI as the raw key, a STATUS item without a result list has no options, and
an address in the wrong range is read from the wrong function code.

This is a completeness register: every entry here is a decision, and an
entry without one is a red test. What was already wrong on adoption is listed
by name so the NEXT one fails.
"""

from collections import Counter
import json
import pathlib

import pytest

from custom_components.weishaupt_modbus.const import FORMATS, TYPES
from custom_components.weishaupt_modbus.items import ModbusItem
from custom_components.weishaupt_modbus.weishaupt_modbus_api import hpconst
from custom_components.weishaupt_modbus.write_counter_sensor import (
    WRITE_COUNTER_DESCRIPTIONS,
)

PACKAGE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "weishaupt_modbus"
)

PLATFORM_OF = {
    TYPES.SENSOR: "sensor",
    TYPES.NUMBER_RO: "sensor",
    TYPES.SENSOR_CALC: "sensor",
    TYPES.SELECT: "select",
    TYPES.NUMBER: "number",
}

# Input registers (function code 4) live in 3xxxx, holding registers
# (function code 3) in 4xxxx - the client picks the read call by this range.
INPUT_REGISTERS = range(30000, 40000)
HOLDING_REGISTERS = range(40000, 50000)

# The register lists, by name - the ones DEVICELISTS is built from.
LIST_NAMES = [
    "MODBUS_SYS_ITEMS",
    "MODBUS_WP_ITEMS",
    "MODBUS_WW_ITEMS",
    "MODBUS_HZ_ITEMS",
    "MODBUS_HZ2_ITEMS",
    "MODBUS_HZ3_ITEMS",
    "MODBUS_HZ4_ITEMS",
    "MODBUS_HZ5_ITEMS",
    "MODBUS_W2_ITEMS",
    "MODBUS_ST_ITEMS",
    "MODBUS_IO_ITEMS",
]

# Status lists where two numbers share one translation key, so the entity
# cannot tell them apart: EVU lock (10) and SG tariff (11) both read as
# `system_operationmode_sgtariff`. Kept as-is on adoption - a fix changes a
# state string automations may match on.
KNOWN_SHARED_STATUS_KEYS: set[tuple[str, str]] = set()


def _items(module) -> list:
    return [item for name in LIST_NAMES for item in getattr(module, name)]


# strings.json is the master; every language file has to carry the same keys.
STRINGS = PACKAGE / "strings.json"
TRANSLATION_FILES = (
    STRINGS,
    PACKAGE / "translations" / "en.json",
    PACKAGE / "translations" / "de.json",
    PACKAGE / "translations" / "nl.json",
)


def _entity_translations(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["entity"]


def _signature(item: ModbusItem) -> tuple:
    return (
        item.address,
        item.name,
        item.format,
        item.type,
        item.device,
        item.translation_key,
    )


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda p: p.name)
def test_every_item_has_a_translated_name(path):
    """An item without an entry shows its raw translation key as the entity
    name - five of them did when this guard was adopted."""
    translations = _entity_translations(path)
    untranslated = sorted(
        item.translation_key
        for item in _items(hpconst)
        if item.translation_key not in translations[PLATFORM_OF[item.type]]
    )

    assert not untranslated, (
        f"{path.name}: translation key(s) without an entry: {untranslated}. "
        "Add the entry to strings.json AND every file under translations/."
    )


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda p: p.name)
def test_no_translation_outlives_its_item(path):
    """The other direction: an entry nothing refers to is a name nobody sees
    and a translator's work nobody uses - and it hides a renamed key."""
    translations = _entity_translations(path)
    known = {(PLATFORM_OF[item.type], item.translation_key) for item in _items(hpconst)}
    # The write counters are sensors without a register.
    known |= {("sensor", description.key) for description in WRITE_COUNTER_DESCRIPTIONS}
    orphaned = sorted(
        f"{platform}.{key}"
        for platform in sorted(set(PLATFORM_OF.values()))
        for key in translations.get(platform, {})
        if (platform, key) not in known
    )

    assert not orphaned, (
        f"{path.name}: entry(ies) without an item in hpconst.py: {orphaned}. "
        "Drop them, or restore the item they belonged to."
    )


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda p: p.name)
def test_every_status_value_has_a_state_translation(path):
    """A STATUS entity's state IS the translation key of the matching status
    number; the display text comes from the `state` map under the entity.
    Operating mode 36 was added to the table under one key and to the
    translations under another - every pump in that mode showed the raw key
    (issue #154)."""
    translations = _entity_translations(path)
    mismatched = []
    for item in _items(hpconst):
        if item.format != FORMATS.STATUS or not item.resultlist:
            continue
        translated = set(
            translations[PLATFORM_OF[item.type]]
            .get(item.translation_key, {})
            .get("state", {})
        )
        listed = {status.translation_key for status in item.resultlist}
        mismatched += [
            f"{item.translation_key}:{key} (no text)"
            for key in sorted(listed - translated)
        ]
        mismatched += [
            f"{item.translation_key}:{key} (no status)"
            for key in sorted(translated - listed)
        ]

    assert not mismatched, (
        f"{path.name}: status keys and state translations disagree: {mismatched}. "
        "The table and every translation file have to name the same keys."
    )


def test_every_status_item_has_a_result_list():
    """A STATUS item maps numbers to texts; without the list every value
    reads as `unbekannt <n>`."""
    without = [
        item.translation_key
        for item in _items(hpconst)
        if item.format == FORMATS.STATUS and not item.resultlist
    ]

    assert not without, f"STATUS item(s) without a result list: {without}"


def test_every_address_is_a_register_and_every_writable_one_is_holding():
    """The client picks the read call by address range, so every address has
    to be in one of the two; and a write is refused outside 4xxxx, so a NUMBER
    or SELECT on an input address could never be set."""
    wrong = []
    for item in _items(hpconst):
        in_a_range = (
            item.address in INPUT_REGISTERS or item.address in HOLDING_REGISTERS
        )
        writable = item.type in (TYPES.NUMBER, TYPES.SELECT)
        if not in_a_range or (writable and item.address not in HOLDING_REGISTERS):
            wrong.append((item.address, item.translation_key, item.type))

    assert not wrong, f"address(es) outside the range of their type: {wrong}"


def test_no_translation_key_is_used_twice():
    """The coordinator maps values by translation key; a second item with the
    same key would silently overwrite the first."""
    counts = Counter(item.translation_key for item in _items(hpconst))
    duplicated = sorted(key for key, count in counts.items() if count > 1)

    assert not duplicated, f"translation key(s) defined twice: {duplicated}"


def test_status_numbers_map_to_distinct_translation_keys():
    """Two numbers behind one key are indistinguishable in the entity state."""
    shared = set()
    for name in dir(hpconst):
        value = getattr(hpconst, name)
        if not (isinstance(value, list) and value and hasattr(value[0], "number")):
            continue
        counts = Counter(status.translation_key for status in value)
        shared |= {(name, key) for key, count in counts.items() if count > 1}

    new = shared - KNOWN_SHARED_STATUS_KEYS
    assert not new, f"status number(s) sharing a translation key: {sorted(new)}"
    fixed = KNOWN_SHARED_STATUS_KEYS - shared
    assert not fixed, f"{sorted(fixed)} are distinct now - drop the exemption."


def test_the_signature_sees_a_changed_field():
    """Proof-of-red for the comparison: one changed format is one difference."""
    item = ModbusItem(1, "x", FORMATS.NUMBER, TYPES.SENSOR, "dev", "k")
    changed = ModbusItem(1, "x", FORMATS.TEMPERATURE, TYPES.SENSOR, "dev", "k")

    assert _signature(item) != _signature(changed)


def test_the_second_heat_source_counters_carry_the_register_lists_labels():
    """Upstream labelled 34102 "switching cycles E1", 34103 "operation hours
    E1" and 34106 "switching cycles E2". The Weishaupt register list - and a
    pump whose display showed E1 at 2 h while 34106 read 2 - says otherwise."""
    by_address = {item.address: item for item in hpconst.MODBUS_W2_ITEMS}

    assert by_address[34102].name == "Betriebsstunden 2. WEZ"
    assert by_address[34103].name == "Schaltspiele 2. WEZ"
    assert by_address[34106].name == "Betriebsstunden E1"
    assert by_address[34107].name == "Betriebsstunden E2"
    for hours in (34102, 34106, 34107):
        assert by_address[hours].params == hpconst.PARAMS_TIME_H, hours
    assert not by_address[34103].params, "a cycle count has no unit"


def test_a_pump_without_a_second_heat_source_has_a_state_for_it():
    """Live: 44101 answers 255 on a pump with no second heat source; without
    an entry the sensor showed the raw fallback text as its state."""
    by_address = {item.address: item for item in hpconst.MODBUS_W2_ITEMS}

    assert by_address[44101].get_translation_key_from_number(255) == "w2_konf_255"
    assert by_address[44102].get_translation_key_from_number(5) == "w2_konf_0"
    assert by_address[44103].get_translation_key_from_number(6) == "w2_konf_0"


def _key_paths(tree: dict, prefix: str = "") -> set[str]:
    paths = set()
    for key, value in tree.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths |= _key_paths(value, f"{path}.")
        else:
            paths.add(path)
    return paths


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda p: p.name)
def test_every_language_has_exactly_the_keys_of_strings_json(path):
    """Upstream #184: the languages drifted apart by hand. A key missing in
    one language shows the raw key or the English fallback there; a key only
    one language has is text nobody sees. The entity guards above cover the
    register table; this one covers the rest - config flow, options, devices."""
    reference = _key_paths(json.loads(STRINGS.read_text(encoding="utf-8")))
    translated = _key_paths(json.loads(path.read_text(encoding="utf-8")))

    assert translated == reference, (
        f"{path.name} differs from strings.json: "
        f"missing {sorted(reference - translated)}, extra {sorted(translated - reference)}"
    )


LEGACY_UNIQUE_IDS = pathlib.Path(__file__).parent / "legacy_unique_ids.json"


def test_every_item_keeps_the_name_its_unique_id_is_built_from():
    """The unique id is prefix + item name + postfix, and Home Assistant keys
    an entity's history on it. The snapshot is the table as shipped before the
    rewrite: a renamed item here is a renamed unique id, which orphans the
    entity's history - unless an entry migration moves it (see version 10).
    New items may be added; a deliberate rename updates the snapshot in the
    same commit as its migration."""
    snapshot = json.loads(LEGACY_UNIQUE_IDS.read_text(encoding="utf-8"))
    current = {item.translation_key: item.name for item in _items(hpconst)}

    renamed = {
        key: (snapshot[key], name)
        for key, name in current.items()
        if key in snapshot and snapshot[key] != name
    }
    gone = sorted(set(snapshot) - set(current))
    assert not renamed, f"item name(s) changed - unique ids move: {renamed}"
    assert not gone, f"item(s) gone - their entities orphan: {gone}"


def test_the_status_lists_reach_as_far_as_the_data_point_list():
    """Operating status 0-43 and DHW push up to 240 minutes, per 83807301
    (1/2025-11). Codes 38 and 40-42 were missing and the push list stopped at
    235, so a pump in one of those states showed the raw number."""
    for statuses in (hpconst.SYS_BETRIEBSANZEIGE, hpconst.HP_BETRIEB):
        assert {status.number for status in statuses} >= set(range(44))

    push = {status.number for status in hpconst.WW_PUSH}

    assert push == {0} | set(range(5, 245, 5))


def test_the_constant_flow_temperatures_reach_the_controllers_own_range():
    """These rows shared the room-temperature limits (16-28 degC) and refused
    a live 35 degC. The controller's menu offers 7-66 heating and 7-30
    cooling, and clamps further to its own min/max flow settings."""
    rows = {item.address: item for item in _items(hpconst)}

    for address in (41110, 41111):
        assert rows[address].params["min"] <= 7
        assert rows[address].params["max"] >= 66
    assert rows[41112].params["min"] <= 7
    assert rows[41112].params["max"] >= 30


def test_the_dhw_pump_variant_is_code_8():
    """The manufacturer numbers the pump variant of the DHW configuration 8;
    the table said 2, so a pump system read as an unknown code."""
    assert {status.number for status in hpconst.WW_KONFIGURATION} == {0, 1, 8}
