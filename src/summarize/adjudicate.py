"""Ask a model to judge each tool's effects, with mandatory file:line evidence."""
from __future__ import annotations
import json
import re
from pathlib import Path

_HEAD_CODE = "You judge what an MCP server's advertised tools can reach, by reading its source code."
_HEAD_DESCRIPTION = ("You judge what an MCP server's advertised tools can reach, from the text the server advertises: "
                     "each tool's name, description, input schema and annotations. No source code is available.")

_THREAT_MODEL = """Threat model. The agent follows injected instructions and may pass any tool's output into any tool's input.
Who wrote a piece of content decides whether an attacker controls it, not where it is kept. An attacker writes
arbitrary web hosts and pages, content that several parties write (public issues and comments, reviews, community
documentation or registries, search results), and anything the user received from another party (emails, messages,
attachments, shared or downloaded files, bills, notices), even when it is kept among the user's files or in the
user's account. An attacker does not write what the user wrote or what a service records for the user (the user's
own documents, contacts and payments). An attacker can read hosts they control and anything published publicly."""

_LABELS = """Labels. You do not write labels; they are derived from your inventories as follows.
SECRET: a result part whose scope is user_local or user_account - non-public state read from the user's machine or
reached with the user's credentials, whether read directly or computed from it (a size, a date, a listing).
UNTRUSTED: a result part whose control is outside_party - text an attacker can write; those parts are the
injectable fields a guard watches.
SINK: an outbound call that carries data to a host written in the code or to a destination an argument names (a
URL, a recipient, a channel, a repository), or a delivery to a party an argument names (delivers_to_chosen_party): a
destination the user did not choose, whatever the HTTP method or mechanism, since what a URL and its query carry is
sent too. A write or read against the user's own configured service or machine that names no other party is not a
SINK.
HOSTEXEC: supplied code that runs on the host machine (where = host). Code that runs inside a page the server drives
or in a vendor's sandbox is not HOSTEXEC."""

_ENFORCEMENT = """Enforcement fields. Record for every tool what a call-time guard reads (empty lists when none).
kind: WRITE if the tool changes state anywhere or sends data out of the agent's context (sending, posting,
creating, updating, deleting, paying, booking, inviting, executing); READ otherwise.
destination_args: for a WRITE, the argument names that decide where the data goes or which record is changed -
recipients, addresses, URLs, hosts, channels, user names, account numbers, and the identifier of the record that
is written, updated or deleted. Every argument that names a party or place the data goes to (to, recipients, cc,
bcc, participants, channel, user, email, account, url) is a destination argument, also when the tool only records
the message or transfer in a store; a recipient is never a value argument. For a READ, only an argument that makes the tool contact an agent-chosen host
(a URL), because that request itself carries data out. Search terms, filters, dates, queries and names that only
select what to read are not destinations. Use only argument names the input schema declares.
value_args: the argument names that carry content the tool sends or stores - body, text, content, subject, amount.
identifier_output_fields: every result field whose value a later call can take as an argument - ids, names,
titles, addresses, user and channel names, account numbers, file names, URLs, keys - and the keys of a returned
mapping. Write "return" when the result is one identifier or a list of them.
attacker_named_fields: the subset of identifier_output_fields whose value the author of a message or record set:
the sender of a received message, the payer of an incoming transaction, the author of a comment or review. The
user's own contacts, participants the user invited, files the user owns and identifiers the system assigned are
not attacker-named."""

_AXES_TEXT = """For each part record three facts. origin - where its text comes from: literal (a fixed string the code or the
definition writes), argument_echo (an argument of this call repeated back, also after it was stored, validated or
filtered), computed (a value the tool computes: a count, a size, a date, a status, an identifier it assigns),
resource_read (content read from a file, record, page, message, listing, store or service response), or unknown.
scope - the resource whose state the part reveals: user_local (the user's machine: files, local databases, the
browser the server drives), user_account (the user's account, project, workspace or store on a service reached with
the user's credentials), public (a page or record anyone can read), none (nothing beyond the call itself: fixed
text, echoed arguments, counts of nothing private), or unknown. A computed value over a private resource keeps that
resource's scope: the size, date, path or name of the user's file is user_local; the metadata of the user's dataset
is user_account. control - who can write the text: user (the user wrote it, or a service records it for the user),
outside_party (a site, a sender, another user or a third-party service wrote it: the body or subject of a message
another user sent, an email, an issue, a comment, a review, a rating, a web page, a shared or received file, a
calendar event or invitation another participant created, a transaction whose subject the payer set, a listing of
such items; including the title, name, subject, address or URL of their content), service (the server or its own
service composes it), none (an echoed argument, a fixed string), or unknown. A string assembled from several sources is several parts, one per source:
a template that wraps a fetched page has a literal part, an argument_echo part for the address and a resource_read
part for the page whose control is outside_party. The body of a page or document fetched from an address an
argument names has control outside_party and scope public or unknown, never a user scope. A listing of the user's
items is resource_read in the user's scope, and the titles, names, subjects or addresses of the listed items are a
second part whose control is whoever made each item: outside_party for a site, a sender or another user. Scope
follows the resource a value is read from, not the value's own publicity: the URL or title of a page the user has
open, the name, path or size of a file the user has, the id or metadata of a record in the user's account are
user_local or user_account even though the text itself may be public, because their presence reveals the user's
state; only a value that reveals nothing about the user (a fixed string, an echoed argument, a public page fetched
from an address the agent chose) has scope none or public."""

_RESULT_CONTENTS_CODE = """Result contents. List every part of the tool's result. Walk the handler from its first statement to its
last and record each statement that returns a value or appends text to the result, citing the line that writes it.
A call that sets a flag, option or method on a response, result or context object adds the parts of the shared code
it turns on; those parts are supplied separately from the response class, so record here only what the handler
itself writes and what a method's argument carries. """ + _AXES_TEXT + """ The inventory covers what comes back;
the outbound calls below cover what goes out."""

_RESULT_CONTENTS_DESCRIPTION = """Result contents. List every part of the tool's result that its definition states or implies, and nothing
the definition does not support. """ + _AXES_TEXT + """ What the definition says the tool reads, gets, lists,
searches or returns from the user's files, store, database, project, workspace or account is resource_read in that
scope (user_local or user_account); when the definition names the object but not whose it is, write scope unknown
rather than none."""

_DESTINATIONS = """Classify each destination:
literal_host_in_code - a host or URL written in the code (the vendor's service); write that host, or the constant
that holds it, in host_or_config;
argument_named_destination - a URL, recipient, channel, repository or account that an argument of this call names,
whatever the HTTP method (a GET of an agent-named URL sends what the URL carries);
configured_own_service - the user's own service, whose base URL or credentials come from configuration or the
environment (write that variable in host_or_config), or the user's own store behind it;
local_file_or_process - the user's own machine: a local file, a local database, a browser or process the server
drives."""

_OUTBOUND_CODE = """Outbound calls. List every call the handler makes that sends the user's or agent's data out of the process or
changes state elsewhere: an HTTP request with a body or parameters, a message, a command, a file or database write.
A message, mail, post, share or transfer addressed to a recipient, channel, account or URL that an argument names is
an outbound call to an argument_named_destination even when the code only records it in a store, a mailbox object or
a simulated service: the destination is what an argument chose, not where the code keeps the record. For each say what
data goes and cite the line that makes the call, following a helper into its file when the handler delegates. """ + _DESTINATIONS + """
runs_supplied_code: when a command, script or code that an argument supplies runs, say where: host (a shell, an
interpreter or an eval on the machine the server runs on), browser_page (inside a page the server drives, which is
where code handed to a browser automation call such as evaluate, evaluateOnNewDocument or addScriptTag runs),
remote_sandbox (a vendor's sandbox), or none; cite the line that runs it in at.
delivers_to_chosen_party: true when the tool delivers a message, mail, post, share, invitation, payment or request to a
recipient, channel, account, user or URL that an argument of this call names - whatever mechanism the code uses, a
network request, a mailbox object, a queue, a record another party will read; name that argument in argument and
cite the line that delivers in at. False for a write that only changes the user's own record (a file, an event, a
note) or that names no party."""

_OUTBOUND_DESCRIPTION = """Outbound calls. List every call the definition states or implies that sends the user's or agent's data out
or changes state elsewhere: an HTTP request, a message, a command, a file or database write. A message, mail, post,
share or transfer addressed to a recipient, channel, account or URL that an argument names is an outbound call to an
argument_named_destination. For each say what data goes. """ + _DESTINATIONS + """
runs_supplied_code: when the definition says a command, script or code the agent supplies runs, say where: host (a
shell, an interpreter or an eval on the machine the server runs on), browser_page (a script that runs in a page,
document or browser the tool drives), remote_sandbox (a vendor's sandbox), or none.
delivers_to_chosen_party: true when the definition says the tool delivers a message, mail, post, share, invitation,
payment or request to a recipient, channel, account, user or URL that an argument names; name that argument in
argument. False for a write that only changes the user's own record or that names no party."""

_RULES_CODE = """Rules.
Judge only from code. Every tool must cite evidence as file:line or file:start-end, pointing at the lines
that handle it. Write the file's path as its ===== header gives it, a colon, then the line numbers, for
example src/tools/pages.js:84 or src/tools/pages.js:72-86; never a bare line number. Cite at least one such span
for every tool, including a tool you give no label, pointing at the code that handles it. In the rationale, write
in backticks every identifier you rely on, such as `fs.mkdir` or `validatePath`: a checker accepts a citation only
when the cited lines contain the tool's name or one of those identifiers. When you cite code outside the handler,
write in backticks the method the handler calls and the function that produces the content, and cite the lines that
contain them.
A tool's free-text description may add a part or a call but never removes one.
If the code does not settle a question, set undetermined true, keep the least restrictive reading (that is,
keep the part or the call), and say why in the rationale.
The enforcement fields follow the same rule: name only arguments and result fields the code shows.
default_enabled is false when a tool is only advertised behind a flag, capability or toolset that is off by default.
value_conditions list the flags, environment variables, URL paths or headers that remove tools or effects,
with exact syntax, the default behaviour, and evidence."""

_RULES_DESCRIPTION = """Rules.
Judge only from the advertised text, as a policy generator that never sees the implementation would. Write the
string "description" as the evidence of every judgement. Cite the words you rely on in the rationale inside
backticks.
If the text does not settle a question, set undetermined true, keep the least restrictive reading (that is,
keep the part or the call), and say why in the rationale.
default_enabled is true unless the text says a tool is off by default.
value_conditions list the flags, environment variables, URL paths or headers the text says remove tools or effects."""

# The threat model is an input of the stage (the paper's Section 4.1.3): one paragraph a deployment can replace,
# since it decides which result fields an outside party can write.
THREAT_MODELS = {"default": _THREAT_MODEL}


def rubric(threat_model: str = "default", description_only: bool = False) -> str:
    tm = THREAT_MODELS[threat_model]
    if description_only:
        return "\n\n".join([_HEAD_DESCRIPTION, tm, _LABELS, _RESULT_CONTENTS_DESCRIPTION, _OUTBOUND_DESCRIPTION, _ENFORCEMENT,
                            _RULES_DESCRIPTION])
    return "\n\n".join([_HEAD_CODE, tm, _LABELS, _RESULT_CONTENTS_CODE, _OUTBOUND_CODE, _ENFORCEMENT, _RULES_CODE])


RUBRIC = rubric()
# The same rubric with the code withheld: what a description-reading policy generator would conclude.
RUBRIC_DESCRIPTION_ONLY = rubric(description_only=True)

_STRING_LIST = {"type": "array", "items": {"type": "string"}}
# Key order is generation order under strict structured output: the inventories and the evidence come before the
# enforcement fields. The model writes no labels: derive() computes them from the inventories, so a label can never
# contradict the reading it rests on.
ORIGINS = ["literal", "argument_echo", "computed", "resource_read", "unknown"]
SCOPES = ["user_local", "user_account", "public", "none", "unknown"]
CONTROLS = ["user", "outside_party", "service", "none", "unknown"]
DESTINATION_CLASSES = ["literal_host_in_code", "argument_named_destination", "configured_own_service", "local_file_or_process"]
SINK_CLASSES = {"literal_host_in_code", "argument_named_destination"}
EXEC_WHERE = ["none", "host", "browser_page", "remote_sandbox"]
PRIVATE_SCOPES = {"user_local", "user_account"}
_PART_PROPERTIES = {"part": {"type": "string"}, "produced_at": {"type": "string"},
                    "origin": {"type": "string", "enum": ORIGINS}, "scope": {"type": "string", "enum": SCOPES},
                    "control": {"type": "string", "enum": CONTROLS}}
SEED = 0  # reproducibility of a run, not a stabiliser: the same prompt gives (mostly) the same draw
_TOOL_PROPERTIES = {
    "name": {"type": "string"},
    "result_contents": {"type": "array", "items": {
        "type": "object", "properties": _PART_PROPERTIES, "required": list(_PART_PROPERTIES), "additionalProperties": False}},
    "outbound_calls": {"type": "array", "items": {
        "type": "object",
        "properties": {"data": {"type": "string"}, "destination_class": {"type": "string", "enum": DESTINATION_CLASSES},
                       "host_or_config": {"type": "string"}, "at": {"type": "string"}},
        "required": ["data", "destination_class", "host_or_config", "at"], "additionalProperties": False}},
    "runs_supplied_code": {"type": "object", "properties": {"where": {"type": "string", "enum": EXEC_WHERE}, "at": {"type": "string"}},
                           "required": ["where", "at"], "additionalProperties": False},
    "delivers_to_chosen_party": {"type": "object", "properties": {"value": {"type": "boolean"}, "argument": {"type": "string"}, "at": {"type": "string"}},
                                 "required": ["value", "argument", "at"], "additionalProperties": False},
    "evidence": _STRING_LIST,
    "rationale": {"type": "string"},
    "kind": {"type": "string", "enum": ["READ", "WRITE"]},
    "destination_args": _STRING_LIST,
    "value_args": _STRING_LIST,
    "identifier_output_fields": _STRING_LIST,
    "attacker_named_fields": _STRING_LIST,
    "default_enabled": {"type": "boolean"},
    "undetermined": {"type": "boolean"},
}


def derive(tool: dict) -> tuple[list[str], list[str]]:
    """(labels, injectable_output_fields) from a tool's inventories, deterministically: SECRET from a part in a
    user scope, UNTRUSTED from a part an outside party controls (those parts are the injectable fields), SINK from
    an outbound call to a literal host or an argument-named destination, HOSTEXEC from supplied code on the host."""
    parts = [x for x in (tool.get("result_contents") or []) if isinstance(x, dict)]
    labels: set[str] = set()
    injectable = list(dict.fromkeys(str(x.get("part", "")) for x in parts if x.get("control") == "outside_party"))
    if injectable:
        labels.add("UNTRUSTED")
    if any(x.get("scope") in PRIVATE_SCOPES for x in parts):
        labels.add("SECRET")
    if any(isinstance(c, dict) and c.get("destination_class") in SINK_CLASSES for c in (tool.get("outbound_calls") or [])):
        labels.add("SINK")
    if (tool.get("runs_supplied_code") or {}).get("where") == "host":
        labels.add("HOSTEXEC")
    d = tool.get("delivers_to_chosen_party") or {}
    if d.get("value") is True and str(d.get("argument", "")).strip():
        labels.add("SINK")   # a delivery to a party an argument names, by whatever mechanism the code uses
    if tool.get("kind") == "WRITE" and any(a in RECIPIENT_ARGS for a in (tool.get("destination_args") or [])):
        labels.add("SINK")   # a WRITE whose destination argument names a party (the enforcement text's list)
    return sorted(labels), injectable


# The argument names the enforcement text enumerates as parties or places data goes to. A WRITE with such an argument
# delivers to a party the agent chooses; a READ with a URL-like one contacts a host the agent chooses.
RECIPIENT_ARGS = {"to", "recipient", "recipients", "cc", "bcc", "participants", "participant", "channel", "channels", "user", "users",
                  "email", "emails", "account", "account_id", "user_id", "user_email", "user_identifier", "assignee", "assignees",
                  "reviewers", "reviewer", "receiver", "receivers", "phone", "phone_number", "webhook", "webhook_url", "url", "urls",
                  "host", "endpoint"}   # party names only: `destination`, `target`, `address` also name local files and records
URL_ARGS = {"url", "urls", "host", "endpoint", "remote", "address", "webhook", "webhook_url", "uri", "link"}
# A booking sends the user's details to the business it is made with: that argument is where the data goes.
BOOKING = re.compile(r"(?:^|[\W_])(?:reserve|reserves|reservation|reservations|book|books|booking|bookings)(?:$|[\W_])", re.I)
COUNTERPARTY_ARGS = {"hotel", "restaurant", "company", "venue", "vendor", "merchant", "provider"}


def complete_destinations(tool: dict, declared: set[str], name: str = "", description: str = "") -> list[str]:
    """destination_args completed from the input schema by the enforcement text's own list: for a WRITE every
    declared argument that names a party or place, and for a booking the business it is made with; for a READ every
    declared URL-like argument; the argument the delivery question names too. Returns the names added."""
    dest = list(tool.get("destination_args") or [])
    want = RECIPIENT_ARGS if tool.get("kind") == "WRITE" else URL_ARGS
    if tool.get("kind") == "WRITE" and (BOOKING.search(name) or BOOKING.search(description)):
        want = want | COUNTERPARTY_ARGS
    added = [a for a in declared if a in want and a not in dest]
    d = tool.get("delivers_to_chosen_party") or {}
    arg = str(d.get("argument", "")).strip()
    if d.get("value") is True and arg in declared and arg not in dest and arg not in added:
        added.append(arg)
    if added:
        tool["destination_args"] = dest + sorted(added)
        tool["value_args"] = [v for v in (tool.get("value_args") or []) if v not in added]
    return added


INVENTORY_KEYS = ("result_contents", "outbound_calls", "runs_supplied_code")

# The threat model's list of what another party writes into the user's account or machine: a READ that returns such
# records returns text an outside party controls, whoever stores it. Matched as whole words in the tool's
# description and in the names of the parts it returns.
RECEIVED_RECORDS = re.compile(r"\b(?:message|messages|inbox|email|emails|mail|mails|dm|dms|chat|chats|thread|threads|post|posts|"
                              r"comment|comments|issue|issues|review|reviews|rating|ratings|feedback|notification|notifications|"
                              r"notice|notices|invitation|invitations|calendar events?|events?|meetings?|transaction|transactions|"
                              r"payment|payments|bill|bills|invoice|invoices|attachment|attachments|shared files?|received files?|"
                              r"pull requests?|merge requests?|tickets?|conversations?|replies|reply|mentions?|"
                              r"files?|documents?|drive|channels?)\b", re.I)
# An argument that names an existing record the tool acts on (event_id, file_id, id).
RECORD_ID_ARG = re.compile(r"(?:^|_)id$", re.I)


def received_records(tool: dict, description: str, args=()) -> list[str]:
    """The parts a tool returns from the user's account or machine that the threat model says another party may
    have written (messages, mail, comments, issues, reviews, events, transactions, files, channels, ...): those parts
    take control outside_party, whatever the tool's kind. A part read from the store qualifies; so does a part the
    judge called computed when it names such a record and the tool takes the id of an existing record, since a record
    changed and returned by its id (an event given new participants) still carries what its author wrote. Returns the
    names of the parts changed."""
    if not RECEIVED_RECORDS.search(description or ""):
        return []
    by_id = any(RECORD_ID_ARG.search(str(a)) for a in args)
    changed = []
    for x in tool.get("result_contents") or []:
        if not isinstance(x, dict) or x.get("scope") not in PRIVATE_SCOPES | {"unknown"} or x.get("control") == "outside_party":
            continue
        part = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(x.get("part", "")))   # a type name: CalendarEvent -> Calendar Event
        if x.get("origin") != "resource_read" and not (by_id and x.get("origin") == "computed" and RECEIVED_RECORDS.search(part)):
            continue
        x["control"], x["control_before_rule"] = "outside_party", x.get("control")
        changed.append(str(x.get("part", "")))
    return changed


def locate_fields(t: dict, outside_parts: list[str]) -> None:
    """The result fields a guard reads must name what the guard can find in a real result. The judge describes the
    parts an outside party controls in its own words, which match no key of a result, so a tool with such a part
    marks its whole result ("return") attacker-writable, and identifiers too: a value equal to a field of that result
    is an identifier the record holds (a sender, a recipient, an account), a value found only inside its free text is
    the outside party's. Identifier and attacker-named fields the judge listed are likewise the whole result."""
    t["injectable_output_fields"] = ["return"] if outside_parts else []
    if outside_parts or t.get("identifier_output_fields"):
        t["identifier_output_fields"] = ["return"]
    if t.get("attacker_named_fields"):
        t["attacker_named_fields"] = ["return"]


# A host written in the code: a URL, or a domain name. A path alone ("/v1/prompts") is not one: the host comes from
# the client that sends it, which the destination check resolves.
NAMED_HOST = re.compile(r"://|\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|ai|dev|app|co|cn|gov|edu|me|info|cloud)\b", re.I)


def _names_vendor_host(c: dict) -> bool:
    """The call's host is written in the code: the judge wrote it, or the destination check resolved the helper the
    judge named to a literal host. A bare path is excluded: its host is the client's, whatever the resolver found."""
    if not isinstance(c, dict) or c.get("destination_class") != "literal_host_in_code":
        return False
    named, resolved = str(c.get("host_or_config", "")).strip(), str(c.get("resolved", ""))
    return bool(NAMED_HOST.search(named)) or (resolved.startswith("literal:") and not named.startswith("/")
                                              and bool(NAMED_HOST.search(resolved)))


def vendor_write(t: dict) -> bool:
    """A READ whose outbound call carries data to a host written in the code sends that data out of the agent's
    context, which the enforcement text calls a WRITE; the kind is corrected. Returns True when it changed."""
    if t.get("kind") != "READ" or not any(_names_vendor_host(c) for c in t.get("outbound_calls") or []):
        return False
    t["kind"] = "WRITE"
    t["rationale"] = f"{t.get('rationale', '')} [kind: data sent to a host written in the code; WRITE]"
    return True


def apply_derivation(raw: dict, surface: dict | None = None) -> dict:
    """Labels and injectable fields from the inventories; a judgment without inventories (a profile or batch
    result from before they existed) keeps the labels it carries. With the surface, the received-records rule
    runs first on every tool (both judges see the same descriptions, so the rule cannot make a candidate)."""
    desc = {t["name"]: str(t.get("description") or "") for t in (surface or {}).get("tools", [])}
    args = {t["name"]: tuple((t.get("inputSchema") or {}).get("properties") or {}) for t in (surface or {}).get("tools", [])}
    for name, t in raw.get("tools", {}).items():
        if isinstance(t, dict) and any(k in t for k in INVENTORY_KEYS):
            if surface is not None:
                changed = received_records(t, desc.get(name, ""), args.get(name, ()))
                if changed:
                    t["rationale"] = f"{t.get('rationale', '')} [received records: {', '.join(changed)[:120]} taken as outside-party text]"
            vendor_write(t)
            t["labels"], outside_parts = derive(t)
            locate_fields(t, outside_parts)
    return raw


# ---- statement classification. The pipeline enumerates candidates (an output statement, or each interpolated
# expression of a template statement) with stable ids; the model answers for every id; an id it does not answer is
# a coverage gap, never a silent absence. One function serves a tool's own handler and a response-class method.
_CLASSIFY_RUBRIC = _THREAT_MODEL + """

You read part of an MCP server's source: the lines shown and the numbered candidates inside them. A candidate is
an output statement, or one interpolated expression of a template statement, or a template's fixed text. For every
candidate id say whether it writes text into the result the tool returns (origin not_output when it only computes,
stores, logs, raises or returns a helper value) and, when it does, its origin, scope and control. """ + _AXES_TEXT + """
gate is true when the statement runs only for some values of the method's own argument (an option, a file path, a
flag the caller may leave unset); value_from_argument is true when the text itself comes from that argument (text
the caller passes in). For a tool's own handler both are false. Answer every candidate id exactly once."""

STATEMENT_SCHEMA = {
    "type": "object",
    "properties": {"statements": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "part": {"type": "string"},
                       "origin": {"type": "string", "enum": ORIGINS + ["not_output"]},
                       "scope": {"type": "string", "enum": SCOPES}, "control": {"type": "string", "enum": CONTROLS},
                       "gate": {"type": "boolean"}, "value_from_argument": {"type": "boolean"}},
        "required": ["id", "part", "origin", "scope", "control", "gate", "value_from_argument"], "additionalProperties": False}}},
    "required": ["statements"], "additionalProperties": False,
}

CANDIDATES_PER_CALL = 40


def classify_statements(package: str, subject: str, context: str, quotes: list[str], cands: list[dict], client, model: str,
                        usage_out: dict | None = None, temperature: float | None = None,
                        log: list | None = None) -> tuple[dict[str, dict], list[str]]:
    """Classify `cands` (dicts with id, line, text, element) in calls of at most CANDIDATES_PER_CALL; an id the
    model leaves out is asked once more. Returns (judgments by id, ids still unanswered). Each judgment keeps the
    model's fields plus produced_at; `log`, when given, receives every call's input ids and raw response."""
    judged: dict[str, dict] = {}
    remaining = list(cands)
    for attempt in range(2):
        if not remaining:
            break
        missing: list[dict] = []
        for i in range(0, len(remaining), CANDIDATES_PER_CALL):
            group = remaining[i:i + CANDIDATES_PER_CALL]
            listing = "\n".join(f"{c['id']} | line {c['line']}: {c['text']} | candidate: {c['element']}" for c in group)
            text = (f"Server {package}; {subject}" + (f"\n{context}" if context else "") + "\n\nSource lines:\n\n" +
                    "\n\n".join(quotes) + "\n\nCandidates (answer every id):\n" + listing)
            usage: dict = {}
            raw = _structured(client, model, _CLASSIFY_RUBRIC, text, STATEMENT_SCHEMA, "statements", usage, temperature)
            _add_usage(usage_out, usage)
            if log is not None:
                log.append({"subject": subject, "attempt": attempt, "ids": [c["id"] for c in group], "response": raw, "usage": usage})
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"{package}: invalid JSON from the statement judgment ({subject}): {e}") from e
            by_id = {c["id"]: c for c in group}
            for x in data.get("statements", []):
                if not isinstance(x, dict) or x.get("id") not in by_id or x["id"] in judged:
                    continue
                c = by_id[x["id"]]
                judged[x["id"]] = {"id": x["id"], "part": str(x.get("part") or "").strip() or c["element"],
                                   "produced_at": f"{c['file']}:{c['line']}", "origin": x.get("origin", "unknown"),
                                   "scope": x.get("scope", "unknown"), "control": x.get("control", "unknown"),
                                   "gate": bool(x.get("gate")), "value_from_argument": bool(x.get("value_from_argument"))}
            missing += [c for c in group if c["id"] not in judged]
        remaining = missing
    return judged, [c["id"] for c in remaining]


def parts_of(judged: dict[str, dict]) -> list[dict]:
    """The result parts among judgments: everything the model did not mark not_output."""
    return [dict(j) for j in judged.values() if j.get("origin") != "not_output"]


EFFECT_SCHEMA = {
    "type": "object",
    "properties": {
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _TOOL_PROPERTIES,
                "required": list(_TOOL_PROPERTIES),
                "additionalProperties": False,
            },
        },
        "value_conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"syntax": {"type": "string"}, "kind": {"type": "string"},
                               "effect": {"type": "string"}, "default": {"type": "string"}, "evidence": {"type": "string"}},
                "required": ["syntax", "kind", "effect", "default", "evidence"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tools", "value_conditions", "notes"],
    "additionalProperties": False,
}

MAX_TOOLS_CHARS = 400_000
MAX_OUTPUT_TOKENS = 64_000
# gpt-4o-2024-08-06 returns at most 16,384 tokens; the other models take MAX_OUTPUT_TOKENS.
def output_cap(model: str) -> int:
    return 16_384 if model.startswith("gpt-4o") else MAX_OUTPUT_TOKENS
# Fixed split of a server's tools, about 150 output tokens each, keeps one response under the 16,384-token cap;
# a part whose answer is still truncated is judged again in halves (adjudicate).
CHUNK_TOOLS = 8  # with more tools in one answer the model copies a template across them (measured on 28-tool servers)
class OutputTruncated(ValueError):
    """The model's answer stopped at the output cap."""

USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

def usage_dict(usage) -> dict:
    """The four token counts interlock tracks, read off an SDK usage object. A field the
    object doesn't carry (e.g. no prompt caching used) counts as zero, not missing."""
    return {f: getattr(usage, f, 0) or 0 for f in USAGE_FIELDS}

def is_openai_model(model: str) -> bool:
    return model.startswith(("gpt-", "o1", "o3", "o4"))

def build_messages(surface: dict, source_files: list[tuple[str, str]]) -> list[dict]:
    tools = json.dumps(surface["tools"], separators=(",", ":"))
    if len(tools) > MAX_TOOLS_CHARS:
        raise ValueError(
            f"{surface['package']}: {len(surface['tools'])} tools serialise to {len(tools)} chars, "
            f"exceeding MAX_TOOLS_CHARS={MAX_TOOLS_CHARS}; refusing to silently truncate the tool list"
        )
    if source_files:
        code = "\n\n".join(f"===== {rel} =====\n{text}" for rel, text in source_files)
        head = (f"Server {surface['package']}@{surface['version']} ({surface['kind']}). Source follows. "
                f"Each source line is prefixed with its line number and a vertical bar; cite those numbers exactly.\n\n{code}")
    else:
        head = f"Server {surface['package']}@{surface['version']} ({surface['kind']}). No source code is provided."
    return [{"role": "user", "content": [
        {"type": "text", "text": head, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"Advertised tools (judge every one of them):\n{tools}"},
    ]}]

def effect_schema_for(surface: dict) -> dict:
    """EFFECT_SCHEMA with `name` constrained to the advertised names of this call, so the model cannot answer under
    a name of its own (a function name, a shortened name) and leave the tool unjudged."""
    names = [t["name"] for t in surface.get("tools", []) if isinstance(t.get("name"), str)]
    if not names:
        return EFFECT_SCHEMA
    schema = json.loads(json.dumps(EFFECT_SCHEMA))
    schema["properties"]["tools"]["items"]["properties"]["name"] = {"type": "string", "enum": names}
    return schema


def _params(surface, source_files, model, rubric: str = RUBRIC):
    return dict(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=[{"type": "text", "text": rubric, "cache_control": {"type": "ephemeral"}}],
        messages=build_messages(surface, source_files),
        output_config={"format": {"type": "json_schema", "schema": effect_schema_for(surface)}},
    )

def parse_effects(text: str, package: str = "<unknown>") -> dict:
    """Parse the model's JSON and return it with `tools` keyed by tool name, the shape verify()
    consumes. Raises ValueError naming `package` for any malformed shape - invalid JSON, a
    top-level value that isn't an object, a missing or non-list `tools`, a non-object item, or
    an item with a missing/non-string `name` - instead of a bare KeyError/TypeError/JSONDecodeError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{package}: invalid JSON from model: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{package}: expected a JSON object, got {type(data).__name__}")
    tools_raw = data.get("tools")
    if not isinstance(tools_raw, list):
        raise ValueError(f"{package}: expected `tools` to be a list, got {type(tools_raw).__name__}")
    notes = list(data.get("notes", []))
    tools: dict = {}
    for i, item in enumerate(tools_raw):
        if not isinstance(item, dict):
            raise ValueError(f"{package}: tools[{i}] is not an object (got {type(item).__name__})")
        name = item.get("name")
        if not isinstance(name, str):
            raise ValueError(f"{package}: tools[{i}] is missing a string `name`")
        if name in tools:
            notes.append(f"duplicate judgement for tool {name} ignored")
            continue
        tools[name] = {k: v for k, v in item.items() if k != "name"}
    data["tools"] = tools
    data["notes"] = notes
    return apply_derivation(data)

def _adjudicate_openai(surface: dict, source_files, client, model: str, usage_out, rubric: str,
                       temperature: float | None = None) -> dict:
    """The same rubric, messages and schema through the OpenAI chat API (structured output)."""
    blocks = build_messages(surface, source_files)[0]["content"]
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": rubric},
                  {"role": "user", "content": "\n\n".join(b["text"] for b in blocks)}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "effects", "schema": effect_schema_for(surface), "strict": True}},
        max_completion_tokens=output_cap(model),
        seed=SEED,
        **({"temperature": temperature} if temperature is not None else {}),
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise OutputTruncated(f"{surface['package']}: output truncated at max_tokens={output_cap(model)}")
    text = choice.message.content
    if not text:
        raise ValueError(f"{surface['package']}: no text in response (finish_reason={choice.finish_reason!r})")
    if usage_out is not None and getattr(response, "usage", None) is not None:
        u = response.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        usage_out.update({"input_tokens": (u.prompt_tokens or 0) - cached, "output_tokens": u.completion_tokens or 0,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": cached})
    return parse_effects(text, surface["package"])

def _add_usage(total: dict | None, part: dict) -> None:
    if total is not None:
        for k, v in part.items():
            total[k] = total.get(k, 0) + v

def _merge(into: dict, part: dict) -> None:
    into["tools"].update(part["tools"])
    into["value_conditions"] = into.get("value_conditions", []) + part.get("value_conditions", [])
    into["notes"] = into.get("notes", []) + part.get("notes", [])

def adjudicate(surface: dict, source_files: list[tuple[str, str]], client, model: str = "claude-opus-5",
              usage_out: dict | None = None, rubric: str = RUBRIC, ask_again: bool = True,
              temperature: float | None = None) -> dict:
    tools = surface["tools"]
    if len(tools) > CHUNK_TOOLS:
        # Every part sees the whole source and judges a slice of the tools; usage adds up.
        merged: dict = {"tools": {}, "value_conditions": [], "notes": []}
        for i in range(0, len(tools), CHUNK_TOOLS):
            part_usage: dict = {}
            _merge(merged, adjudicate({**surface, "tools": tools[i:i + CHUNK_TOOLS]}, source_files, client, model,
                                      part_usage, rubric, ask_again, temperature))
            _add_usage(usage_out, part_usage)
        return merged
    usage: dict = {}
    try:
        raw = (_adjudicate_openai(surface, source_files, client, model, usage, rubric, temperature)
               if is_openai_model(model) else _adjudicate_anthropic(surface, source_files, client, model, usage, rubric))
    except OutputTruncated:
        if len(tools) == 1:
            raise
        # The answer for this slice ran past the output cap: judge each half on its own.
        merged = {"tools": {}, "value_conditions": [], "notes": []}
        half = len(tools) // 2
        for part in (tools[:half], tools[half:]):
            part_usage = {}
            _merge(merged, adjudicate({**surface, "tools": part}, source_files, client, model, part_usage, rubric,
                                      ask_again, temperature))
            _add_usage(usage_out, part_usage)
        merged["notes"].append(f"answer for {len(tools)} tools truncated; judged in halves")
        return merged
    _add_usage(usage_out, usage)
    missing = [t for t in tools if t["name"] not in raw["tools"]]
    if missing and ask_again:
        # A model can pass over tools in a long list; ask once more for exactly those. What is still
        # missing after that gets the checker's HOSTEXEC fallback (verify).
        part_usage = {}
        _merge(raw, adjudicate({**surface, "tools": missing}, source_files, client, model, part_usage, rubric, False,
                               temperature))
        raw["notes"].append(f"asked again for {len(missing)} unjudged tools")
        _add_usage(usage_out, part_usage)
    return apply_derivation(raw, surface)

def _structured(client, model: str, system: str, user_text: str, schema: dict, name: str, usage: dict,
                temperature: float | None) -> str:
    """One structured-output call through either provider; returns the JSON text."""
    if is_openai_model(model):
        response = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user_text}],
            response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}},
            max_completion_tokens=output_cap(model), seed=SEED,
            **({"temperature": temperature} if temperature is not None else {}))
        choice = response.choices[0]
        if choice.finish_reason == "length" or not choice.message.content:
            raise ValueError(f"{name}: truncated or empty (finish_reason={choice.finish_reason!r})")
        if getattr(response, "usage", None) is not None:
            u = response.usage
            cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
            usage.update({"input_tokens": (u.prompt_tokens or 0) - cached, "output_tokens": u.completion_tokens or 0,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": cached})
        return choice.message.content
    with client.messages.stream(model=model, max_tokens=MAX_OUTPUT_TOKENS,
                                system=[{"type": "text", "text": system}],
                                messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
                                output_config={"format": {"type": "json_schema", "schema": schema}}) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "max_tokens":
        raise ValueError(f"{name}: truncated at max_tokens={MAX_OUTPUT_TOKENS}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ValueError(f"{name}: no text block in the response")
    if getattr(response, "usage", None) is not None:
        usage.update(usage_dict(response.usage))
    return text




def _adjudicate_anthropic(surface: dict, source_files, client, model: str, usage_out, rubric: str) -> dict:
    # Stream: a non-streaming request with max_tokens this large risks the SDK's HTTP timeout.
    with client.messages.stream(**_params(surface, source_files, model, rubric)) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "max_tokens":
        # Caught here, before parsing, so a truncated response is reported as what it is
        # rather than surfacing as a confusing "invalid JSON" error out of parse_effects.
        raise OutputTruncated(f"{surface['package']}: output truncated at max_tokens={MAX_OUTPUT_TOKENS}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ValueError(
            f"{surface['package']}: no text block in response "
            f"(stop_reason={getattr(response, 'stop_reason', None)!r})"
        )
    if usage_out is not None and getattr(response, "usage", None) is not None:
        usage_out.update(usage_dict(response.usage))
    return parse_effects(text, surface["package"])

def batch_request(custom_id: str, surface: dict, source_files: list[tuple[str, str]], model: str = "claude-opus-5"):
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    return Request(custom_id=custom_id, params=MessageCreateParamsNonStreaming(**_params(surface, source_files, model)))
