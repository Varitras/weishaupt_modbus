"""The translation sync must keep what was translated and report what moved."""

from custom_components.weishaupt_modbus.translations import _sync_dict


def test_an_existing_translation_is_preserved():
    synced, changed = _sync_dict({"a": "master"}, {"a": "übersetzt"})

    assert synced == {"a": "übersetzt"}
    assert changed is False


def test_a_missing_key_is_added_with_the_master_text_as_placeholder():
    synced, changed = _sync_dict({"a": "master", "b": "new"}, {"a": "übersetzt"})

    assert synced == {"a": "übersetzt", "b": "new"}
    assert changed is True


def test_pruned_key_marks_change_and_is_dropped():
    synced, changed = _sync_dict({"a": "master"}, {"a": "übersetzt", "old": "x"})

    assert synced == {"a": "übersetzt"}
    assert changed is True


def test_nested_dictionaries_are_synced_recursively():
    master = {"entity": {"sensor": {"a": {"name": "A"}, "b": {"name": "B"}}}}
    locale = {"entity": {"sensor": {"a": {"name": "Ä"}}}}

    synced, changed = _sync_dict(master, locale)

    assert synced["entity"]["sensor"]["a"] == {"name": "Ä"}
    assert synced["entity"]["sensor"]["b"] == {"name": "B"}
    assert changed is True
