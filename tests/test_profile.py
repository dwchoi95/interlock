import pytest
from interlock.profile import LABELS, Profile, ToolEffect, expand

def test_hostexec_expands_to_all_three():
    assert expand({"HOSTEXEC"}) == {"HOSTEXEC", "SECRET", "UNTRUSTED", "SINK"}
    assert expand({"SECRET"}) == {"SECRET"}

def test_union_skips_disabled_tools():
    p = Profile(package="x", version="1", kind="npm", source="npm pack", tools={
        "read": ToolEffect(labels=["SECRET"], evidence=["a.js:1"], rationale="reads files"),
        "run": ToolEffect(labels=["HOSTEXEC"], evidence=["a.js:2"], rationale="spawns", default_enabled=False),
    }, value_conditions=[], notes=[])
    assert p.union() == {"SECRET"}
    assert p.union(enabled_only=False) == {"SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"}

def test_union_conservative_default_treats_undetermined_as_hostexec():
    # An unverified clear keeps labels=[] with undetermined=True; the spec says an
    # unverified judgement takes the least-restrictive (HOSTEXEC) default, so the
    # conservative union must not read an undetermined, unverified clear as "no effects".
    p = Profile(package="x", version="1", kind="npm", source="npm pack", tools={
        "maybe": ToolEffect(labels=[], evidence=[], rationale="cleared but unverified", undetermined=True),
    }, value_conditions=[], notes=[])
    assert p.union() == {"HOSTEXEC", "SECRET", "UNTRUSTED", "SINK"}
    assert p.union(conservative=False) == set()
    # The recorded labels themselves must stay untouched - conservative only changes
    # what the *union* computes, never the honesty of the stored judgement.
    assert p.tools["maybe"].labels == []

def test_round_trip_json():
    p = Profile(package="x", version="1", kind="npm", source="npm pack", tools={
        "read": ToolEffect(labels=["SECRET"], evidence=["a.js:1"], rationale="reads files")}, value_conditions=[], notes=[])
    assert Profile.from_json(p.to_json()) == p

def test_rejects_unknown_label():
    with pytest.raises(ValueError):
        ToolEffect(labels=["NETWORK"], evidence=["a.js:1"], rationale="x").validate()

def test_labels_frozen():
    assert LABELS == frozenset({"SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"})

def test_invalid_label_raises_at_construction():
    with pytest.raises(ValueError):
        ToolEffect(labels=["NETWORK"], evidence=["a.js:1"], rationale="x")
