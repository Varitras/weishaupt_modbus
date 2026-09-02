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
KNOWN_SHARED_STATUS_KEYS = {
    ("SYS_BETRIEBSANZEIGE", "system_operationmode_sgtariff"),
    ("IO_KONFIG_OUT", "io_config_out_65535"),
}


def _items(module) -> list:
    return [item for name in LIST_NAMES for item in getattr(module, name)]


# strings.json is the master; every language file has to carry the same keys.
TRANSLATION_FILES = (
    PACKAGE / "strings.json",
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


def test_every_item_carries_a_batch_number():
    """The client groups block reads by the batch field; an item without one
    is skipped by the batching and never read."""
    without = [
        item.translation_key
        for item in _items(hpconst)
        if item.type != TYPES.SENSOR_CALC and item.batch is None
    ]

    assert not without, f"item(s) without a batch number: {without}"


def test_the_signature_sees_a_changed_field():
    """Proof-of-red for the comparison: one changed format is one difference."""
    item = ModbusItem(1, "x", FORMATS.NUMBER, TYPES.SENSOR, "dev", "k")
    changed = ModbusItem(1, "x", FORMATS.TEMPERATURE, TYPES.SENSOR, "dev", "k")

    assert _signature(item) != _signature(changed)
