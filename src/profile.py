"""Provider effect profiles: what each advertised tool can reach, and why we believe it."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict

LABELS = frozenset({"SECRET", "UNTRUSTED", "SINK", "HOSTEXEC"})
ALL3 = frozenset({"SECRET", "UNTRUSTED", "SINK"})

def expand(labels: set[str]) -> set[str]:
    """HOSTEXEC reaches everything a host process can reach."""
    return set(labels) | ALL3 if "HOSTEXEC" in labels else set(labels)

@dataclass(eq=True)
class ToolEffect:
    labels: list[str]
    evidence: list[str]
    rationale: str
    default_enabled: bool = True
    undetermined: bool = False

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "ToolEffect":
        unknown = set(self.labels) - LABELS
        if unknown:
            raise ValueError(f"unknown labels: {sorted(unknown)}")
        return self

@dataclass(eq=True)
class Profile:
    package: str
    version: str
    kind: str
    source: str
    tools: dict[str, ToolEffect]
    value_conditions: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def union(self, enabled_only: bool = True, conservative: bool = True) -> set[str]:
        """The reachable-effects union. `conservative` (the safe default) makes an
        undetermined judgement count as HOSTEXEC for this computation only: the
        recorded `labels` on the tool are never touched, so the profile itself stays
        an honest record of what was actually determined, but a downstream consumer
        computing "what can this tool surface reach" is not told less than it might
        reach just because the model's clearance or claim could not be verified."""
        out: set[str] = set()
        for t in self.tools.values():
            if enabled_only and not t.default_enabled:
                continue
            out |= expand(set(t.labels))
            if conservative and t.undetermined:
                out |= expand({"HOSTEXEC"})
        return out

    def to_json(self) -> str:
        d = asdict(self)
        d["tools"] = {k: asdict(v) for k, v in self.tools.items()}
        return json.dumps(d, indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Profile":
        d = json.loads(text)
        tools = {k: ToolEffect(**v).validate() for k, v in d.pop("tools").items()}
        return cls(tools=tools, **d)
