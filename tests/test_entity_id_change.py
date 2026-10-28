"""Every entity of ours is renamed in place.

From Home Assistant 2026.11 a changed entity id moves the entity in place.
An entity class from outside Home Assistant that defines one of the add or
remove methods is still removed and added again, unless it, or a subclass of
it, defines `async_entity_id_changed`; that fallback goes in 2027.11, and a
class added later without the method would quietly keep it until then.

Driven off the classes the package defines, so a new entity is covered
without anyone remembering to come back.
"""

import importlib
import inspect
import pkgutil

import custom_components.weishaupt_modbus as package
from homeassistant.helpers.entity import Entity

# As Home Assistant's _entity_class_requires_readd names them (2026.11).
ADD_OR_REMOVE = (
    "async_prepare_to_add_to_hass",
    "async_added_to_hass",
    "async_will_remove_from_hass",
)
HOOK = "async_entity_id_changed"


def _entity_classes() -> list[type[Entity]]:
    found = []
    for module_info in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
        module = importlib.import_module(module_info.name)
        found += [
            member
            for _, member in inspect.getmembers(module, inspect.isclass)
            if issubclass(member, Entity) and member.__module__ == module.__name__
        ]
    return found


def _removed_and_added_again(entity_class: type[Entity]) -> bool:
    """Home Assistant's rule: an add or remove method of a class outside
    Home Assistant is covered only by the hook in that class or below it."""
    ours = [
        owner
        for owner in entity_class.__mro__
        if not owner.__module__.startswith("homeassistant.")
    ]
    hook_owners = [owner for owner in ours if HOOK in owner.__dict__]
    return any(
        any(method in owner.__dict__ for method in ADD_OR_REMOVE)
        and not any(issubclass(hook_owner, owner) for hook_owner in hook_owners)
        for owner in ours
    )


def test_the_scan_finds_the_entities_that_add_themselves():
    """A scan that finds nothing passes; these three define an add method."""
    names = {entity_class.__name__ for entity_class in _entity_classes()}

    assert {"MySetpointSwitchEntity", "TrafficSensor", "WriteCounterSensor"} <= names


def test_every_entity_is_renamed_in_place():
    removed = [
        entity_class.__name__
        for entity_class in _entity_classes()
        if _removed_and_added_again(entity_class)
    ]

    assert not removed, f"renamed by removing and adding again: {removed}"
