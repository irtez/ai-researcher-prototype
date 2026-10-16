"""Runner-owned invocation of the result critics and the iteration planner.

Design: ``docs/plans/2026-08-18-self-improving-loop-verification.md`` (Approved).

The whole point of this module is G6: **the audited party neither invokes the
critics nor writes their briefs**. The four prompts are in
``sync_claude_agents.py::SKIP``, so they never reach ``.claude/agents/`` and never
enter ``options.agents`` - the manager cannot delegate to them. They are loaded
straight from YAML here instead, the same way ``run_research.py`` loads the
manager prompt from ``system.yaml``.

Briefs are built by :func:`build_analysis_brief` and friends and nothing else.
That is what makes SC4 mechanical rather than honour-system: the builders refuse
to emit a brief that leaks a claimed value into the blind critic's payload, or
that leaves a claim's source file inside its read scope.

MVP is advisory. Nothing here blocks a run: the critics observe, the comparator
records a verdict, the planner drafts, and a human reads the result.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml
from cc_config import cli_environment

PROJECT_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_DIR / "holosophos" / "holosophos" / "prompts"

# The prompt stems this module owns. Each must also be in sync_claude_agents.SKIP;
# tests/test_critic_agent_isolation.py asserts that, and asserts the manager's
# agent set contains none of them.
RUNNER_OWNED = ("analysis_critic", "paper_critic", "sufficiency", "planner")

CRITIC_PROMPTS = {
    "analysis": "analysis_critic",
    "paper": "paper_critic",
    "sufficiency": "sufficiency",
}


class BriefError(ValueError):
    """A brief that would defeat the property it exists to protect."""


def resolve_cli() -> str | None:
    """The Claude CLI to spawn, preferring the real binary over a batch shim.

    On Windows ``shutil.which("claude")`` returns ``claude.CMD``, a batch wrapper
    around the real executable. **Batch mangles multi-line arguments** - CMD
    treats a newline as a command separator - so a system prompt with newlines
    in it never reaches the CLI intact, and the SDK then waits out its full
    60-second handshake and reports ``Control request timeout: initialize``.

    Every critic prompt is multi-line, so via the shim every critic times out
    before making a single model call. Measured 2026-08-20: the same 4210-char
    prompt fails at 70.2s through ``claude.CMD`` and succeeds in 3.6s through
    ``claude.exe``. (The shim is unhealthy generally - even a single-line prompt
    exits 255 through it.)

    ``CLAUDE_CLI_PATH`` overrides, for hosts where this guess is wrong.
    """
    explicit = os.environ.get("CLAUDE_CLI_PATH")
    if explicit:
        return explicit

    found = shutil.which("claude")
    if not found:
        return None
    if Path(found).suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return found

    # Mirrors what the shim itself execs.
    real = (
        Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    )
    return str(real) if real.is_file() else found


# ── prompt loading ────────────────────────────────────────────────────────────


def load_runner_prompt(stem: str) -> dict[str, Any]:
    """Compose a runner-owned prompt from its YAML, without going via .claude/agents/.

    Mirrors ``run_research.py::load_manager_prompt``: canon.role + canon.guidelines
    + canon.output_format + claude_code.prompt_extras.
    """
    if stem not in RUNNER_OWNED:
        raise BriefError(f"{stem!r} is not a runner-owned prompt; expected one of {RUNNER_OWNED}")
    data = yaml.safe_load((PROMPTS_DIR / f"{stem}.yaml").read_text(encoding="utf-8"))
    canon = data.get("canon") or {}
    cc = data.get("claude_code") or {}
    sections = [
        canon.get("role", ""),
        canon.get("guidelines", ""),
        canon.get("output_format", ""),
        cc.get("prompt_extras", ""),
    ]
    return {
        "description": cc.get("description") or canon.get("description") or stem,
        "prompt": "\n\n".join(s.strip() for s in sections if s and s.strip()),
        "model": cc.get("model"),
        "maxTurns": cc.get("maxTurns"),
        "effort": cc.get("effort"),
        "tools": cc.get("tools"),
    }


def build_agent_definition(stem: str) -> Any:
    """An SDK ``AgentDefinition`` for one runner-owned prompt.

    ``mcpServers=[]`` is deliberate: a critic reads the session directory with
    native tools and needs no MCP surface. Passing None would inherit every
    configured server, which is how a read-only auditor quietly acquires the
    ability to write.
    """
    from claude_agent_sdk import AgentDefinition  # imported lazily: unit tests need no SDK

    spec = load_runner_prompt(stem)
    model = None if spec["model"] == "inherit" else spec["model"]
    return AgentDefinition(
        description=spec["description"],
        prompt=spec["prompt"],
        model=model,
        maxTurns=spec["maxTurns"],
        tools=spec["tools"],
        mcpServers=[],
    )


# ── claims and briefs ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Claim:
    """One published quantity, as the runner knows it.

    ``sources`` are the paths the claim is cited to. They are what gets excluded
    from the analysis critic's read scope, and what the paper critic is pointed at.
    """

    claim_id: str
    claimed: str | None = None
    owner: str | None = None
    sources: tuple[str, ...] = ()
    description: str = ""

    def redacted(self) -> dict[str, Any]:
        """The claim as a blind critic may see it: identity and description, no value.

        ``sources`` are dropped too — naming the file a value lives in is a strong
        hint toward the value, and the blind critic is meant to navigate the tree
        itself.
        """
        return {"claim_id": self.claim_id, "description": self.description}

    def for_matching(self) -> dict[str, Any]:
        """The claim as the paper critic sees it: value and cited source together."""
        return {
            "claim_id": self.claim_id,
            "claimed": self.claimed,
            "cited_sources": list(self.sources),
            "description": self.description,
        }


def claims_from_paper_report(report: Any, *, owner: str = "writer") -> list[Claim]:
    """Reuse the paper critic's enumeration as the analysis critic's claim list.

    The paper critic reads the write-up anyway, so it enumerates what is published;
    the runner takes that list, strips the values, and hands the identities to the
    blind critic. This is the seam that keeps a second extractor agent out of the
    design — and it is why `description` is required to be value-free.

    Nothing here trusts the critic to have complied: a description that still
    contains its own claimed value is scrubbed, and if scrubbing cannot make it
    safe the description is dropped entirely rather than shipped. The builder's
    guard would reject the brief anyway; failing here gives the better message.
    """
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except ValueError:
            return []
    if not isinstance(report, dict):
        return []

    claims: list[Claim] = []
    for entry in report.get("claims") or []:
        if not isinstance(entry, dict) or not entry.get("claim_id"):
            continue
        claimed = entry.get("claimed")
        description = str(entry.get("description") or "")
        if claimed not in (None, "") and _mentions(description, str(claimed)):
            description = _scrub(description, str(claimed))
        sources = entry.get("cited_sources") or []
        claims.append(
            Claim(
                claim_id=str(entry["claim_id"]),
                claimed=None if claimed in (None, "") else str(claimed),
                owner=entry.get("owner") or owner,
                sources=tuple(str(s) for s in sources if s),
                description=description,
            )
        )
    return claims


def _mentions(text: str, literal: str) -> bool:
    return bool(re.search(rf"(?<![\w.-]){re.escape(literal)}(?![\w.-])", text))


def _scrub(text: str, literal: str) -> str:
    """Remove a leaked value from a description, or give up on the description."""
    cleaned = re.sub(rf"(?<![\w.-]){re.escape(literal)}(?![\w.-])", "<withheld>", text)
    return "" if _mentions(cleaned, literal) else cleaned


@dataclass(frozen=True)
class CriticBrief:
    """A runner-authored brief. The only accepted input to :func:`run_critic`.

    ``authored_by`` is not decoration: ``run_critic`` refuses anything that is not
    a ``CriticBrief`` carrying ``"runner"``, so a manager-authored string or dict
    cannot be substituted for one (SC4).
    """

    critic: str
    iteration: int
    text: str
    read_scope: str
    excluded: tuple[str, ...] = ()
    authored_by: str = "runner"
    claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.authored_by != "runner":
            raise BriefError(
                f"brief for {self.critic!r} is authored by {self.authored_by!r}, not the runner"
            )


def under(path: str, root: str) -> str:
    """Absolutise a path against the read scope.

    A critic reports the paths it read as it saw them, which means relative to
    its own working directory - ``mle/results/foo.json``, not a host absolute
    path. Resolving those against the *process* cwd instead of the read scope
    puts every one of them outside the session and makes the exclusion guard
    fire on correct input.
    """
    p = Path(path)
    return str(p if p.is_absolute() else Path(root) / p)


def _within(path: str, root: str) -> bool:
    try:
        Path(under(path, root)).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


def _assert_values_withheld(claims: Sequence[Claim], block: str) -> None:
    """No claimed literal may appear in a blind critic's claims block.

    Scoped to the claims block rather than the whole brief: the surrounding
    template carries an iteration number and path fragments of its own, and a
    guard that fires on those would block real runs for no gain.
    """
    for claim in claims:
        if claim.claimed in (None, ""):
            continue
        literal = re.escape(str(claim.claimed))
        if re.search(rf"(?<![\w.-]){literal}(?![\w.-])", block):
            raise BriefError(
                f"claimed value {claim.claimed!r} for {claim.claim_id!r} leaked into the analysis brief; "
                "the critic must derive it, not read it"
            )


def _assert_sources_excluded(
    claims: Sequence[Claim], read_scope: str, excluded: Sequence[str]
) -> None:
    """Every claim's source must be outside the blind critic's read scope.

    Three ways this fails, in the order they are checked: a claim cited outside
    the session directory (nothing in scope can exclude it), a claim with **no**
    recorded source (the runner does not know what to exclude, so the value stays
    readable), and a source that survived into scope.
    """
    excluded = [under(e, read_scope) for e in excluded]
    for entry in excluded:
        if not _within(entry, read_scope):
            raise BriefError(f"excluded path {entry!r} is not inside the read scope {read_scope!r}")
    for claim in claims:
        if claim.claimed not in (None, "") and not claim.sources:
            raise BriefError(
                f"claim {claim.claim_id!r} has a value but no recorded source; "
                "nothing can be excluded, so the critic may read the answer"
            )
        for raw in claim.sources:
            source = under(raw, read_scope)
            if not any(_within(source, entry) or Path(source) == Path(entry) for entry in excluded):
                raise BriefError(
                    f"source {source!r} for claim {claim.claim_id!r} is still readable; "
                    "the analysis critic would read the answer it was asked to derive"
                )


def build_analysis_brief(
    claims: Sequence[Claim],
    *,
    iteration: int,
    session_dir: str,
    previously_flagged: Sequence[Claim] = (),
) -> CriticBrief:
    """Brief the blind critic: what to derive, where to look, what it may not see.

    ``previously_flagged`` claims are re-derivation targets. They carry their
    *currently published* value on purpose — confirming a fix means comparing
    against what the paper now says, and there is nothing left to withhold once a
    defect is already on the record.
    """
    block = json.dumps([c.redacted() for c in claims], indent=2, ensure_ascii=False)
    excluded = sorted({under(s, session_dir) for c in claims for s in c.sources})
    _assert_values_withheld(claims, block)
    _assert_sources_excluded(claims, session_dir, excluded)

    parts = [
        f"Iteration {iteration}. Derive each of the following quantities from the run's records.",
        f"Read scope: {session_dir}",
        "Out of scope (these hold the reported values; deriving is your job):\n"
        + "\n".join(f"  - {e}" for e in excluded),
        "Quantities to derive:\n" + block,
    ]
    if previously_flagged:
        parts.append(
            "Previously flagged — re-derive each and report it under `rederivations`:\n"
            + json.dumps(
                [c.for_matching() for c in previously_flagged], indent=2, ensure_ascii=False
            )
        )
    return CriticBrief(
        critic="analysis",
        iteration=iteration,
        text="\n\n".join(parts),
        read_scope=session_dir,
        excluded=tuple(excluded),
        claim_ids=tuple(c.claim_id for c in claims),
    )


def build_paper_brief(
    claims: Sequence[Claim],
    *,
    iteration: int,
    session_dir: str,
    paper_path: str,
    previously_flagged: Sequence[Claim] = (),
) -> CriticBrief:
    """Brief the matching critic: claim and cited source together, nothing withheld.

    Two modes, and the difference is not cosmetic.

    With ``claims``, the runner already knows what is published and the critic
    only has to match. With none - the replay's case, and any first pass over a
    run - **the critic enumerates the write-up itself**, and that enumeration is
    what the blind analysis critic is later briefed from.

    Handing an empty list to the matching form does not mean "find them
    yourself"; it means "there is nothing to check", and a correct critic will
    report exactly zero. That is what the first live F3 replay did.
    """
    if claims:
        head = [
            f"Iteration {iteration}. Check each published claim against the source it cites.",
            f"Write-up: {paper_path}\nRead scope: {session_dir}",
            "Claims:\n"
            + json.dumps([c.for_matching() for c in claims], indent=2, ensure_ascii=False),
        ]
    else:
        head = [
            f"Iteration {iteration}. Enumerate every quantity this write-up publishes, then check "
            "each one against the source it cites.",
            f"Write-up: {paper_path}\nRead scope: {session_dir}",
            "No claim list is supplied: building it is the first half of your task. Work through "
            "the write-up and record every published quantity in `claims` — abstract, tables, "
            "figure captions, and any number stated in the text — including the ones that turn "
            "out to be correct. `coverage.claims_in` must equal the length of `claims`.\n\n"
            "Your enumeration is reused downstream: a second critic is asked to re-derive these "
            "same quantities from the records without being shown your values. So `claim_id` must "
            "be stable and specific, and `description` must identify the quantity without stating "
            "it.",
        ]
    parts = head
    if previously_flagged:
        parts.append(
            "Previously flagged — re-check each and report it under `rederivations`:\n"
            + json.dumps(
                [c.for_matching() for c in previously_flagged], indent=2, ensure_ascii=False
            )
        )
    return CriticBrief(
        critic="paper",
        iteration=iteration,
        text="\n\n".join(parts),
        read_scope=session_dir,
        claim_ids=tuple(c.claim_id for c in claims),
    )


def build_sufficiency_brief(
    claims: Sequence[Claim],
    *,
    iteration: int,
    session_dir: str,
    task: str,
) -> CriticBrief:
    """Brief the sufficiency critic with the run's original task, verbatim.

    ``task`` is the runner's in-process snapshot taken at start, not a re-read of
    ``started_prompt.md``: that file sits in the manager's own cwd, so reading it
    at critic time would let the audited run choose the question it is judged
    against.

    With no claims supplied, the critic names the cell each finding is about
    rather than being told. The prompt asks for a ``claim_id`` "from the brief",
    so an empty list meant there was nothing to name and every finding came back
    as ``n/a`` -- a correct answer to the wrong question. It also cost the join:
    nothing downstream could tell two findings apart, so a panel could not see
    that two models had found the same thing.
    """
    parts = [
        f"Iteration {iteration}. Judge whether this run's evidence answers the task it was given.",
        "The original task, verbatim:\n" + task,
        f"Read scope: {session_dir}",
    ]
    if claims:
        parts.append(
            "Judge exactly these units, using their ids verbatim:\n"
            + json.dumps([c.redacted() for c in claims], indent=2, ensure_ascii=False)
        )
    else:
        parts.append(
            "No unit list is supplied: building it is the first half of your task. "
            "Enumerate the units of analysis this run's evidence divides into, from the "
            "run's own artifacts, and report them in `units` with a verdict on each -- "
            "including the units that are adequately supported. `coverage.claims_in` must "
            "equal the length of `units`.\n\n"
            "Give each unit a stable `unit_id` you derive yourself, and use it as the "
            "`claim_id` of any finding against that unit. Do not answer `n/a`: the id is "
            "what matches your finding to another critic's, and what the planner traces "
            "its actions back to."
        )
    return CriticBrief(
        critic="sufficiency",
        iteration=iteration,
        text="\n\n".join(parts),
        read_scope=session_dir,
        claim_ids=tuple(c.claim_id for c in claims),
    )


def units_from_sufficiency_report(report: Any) -> list[Claim]:
    """Reuse one critic's enumeration as the unit list the next one is briefed with.

    The same seam as :func:`claims_from_paper_report`, for the same reason. Two
    models enumerating independently invent different ids for the same thing:
    measured 2026-08-20, one called it ``capability_gradient.gpt5mini.env_coverage``
    and the other ``gpt5mini.scienceworld.coverage``, so the panel merge recorded
    zero agreement across ten findings that plainly overlapped. Enumerate once and
    brief both members from that list, and agreement becomes measurable rather
    than invisible.
    """
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except ValueError:
            return []
    if not isinstance(report, dict):
        return []

    units: list[Claim] = []
    seen: set[str] = set()
    for entry in report.get("units") or []:
        if not isinstance(entry, dict):
            continue
        unit_id = str(entry.get("unit_id") or entry.get("claim_id") or "").strip()
        if not unit_id or unit_id in seen:
            continue
        seen.add(unit_id)
        units.append(Claim(claim_id=unit_id, description=str(entry.get("description") or "")))
    return units


def build_planner_brief(
    verdict: dict[str, Any],
    *,
    task: str,
    session_dir: str,
    caps: dict[str, Any] | None = None,
) -> CriticBrief:
    """Brief the planner with the verdict it must trace every action back to."""
    parts = [
        f"Draft the brief for iteration {int(verdict['iteration']) + 1}.",
        "The original task, verbatim:\n" + task,
        "The verdict on the iteration that just finished:\n"
        + json.dumps(verdict, indent=2, ensure_ascii=False),
        f"Read scope: {session_dir}",
        "Every action's `traces_to` must be one of these finding ids: "
        + ", ".join(f["id"] for f in verdict.get("findings", [])),
    ]
    if caps:
        parts.append("Budget and scope caps:\n" + json.dumps(caps, indent=2, ensure_ascii=False))
    return CriticBrief(
        critic="planner",
        iteration=int(verdict["iteration"]) + 1,
        text="\n\n".join(parts),
        read_scope=session_dir,
    )


# ── invocation ────────────────────────────────────────────────────────────────

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def extract_json(text: str) -> Any:
    """Pull the report object out of a model's final text.

    The prompts ask for bare JSON, but a fenced block or a leading sentence is a
    common enough slip that failing the whole verdict over it would be perverse.
    A response with no object at all is returned as-is, so the comparator records
    it as a contract error rather than this layer raising.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    match = _JSON_BLOCK.search(stripped)
    if match:
        try:
            return json.loads(match.group(0))
        except ValueError:
            pass
    return text


async def run_critic(
    brief: Any,
    *,
    model: str | None = None,
    cwd: str | None = None,
    cli_path: str | None = None,
    max_usd: float | None = None,
) -> Any:
    """Invoke one runner-owned agent out of band and return its parsed report.

    Refuses anything that is not a :class:`CriticBrief`. That is the enforcement
    point for "the audited party does not write the brief": the manager can
    produce a string or a dict, but it cannot produce one of these.

    ``model`` overrides the prompt's own choice. It exists for two jobs: a cheap
    shake-out pass that finds contract bugs before an expensive one, and running
    the same critique under a second model family as a panel — which is what
    caught 2 of the 13 defects in the recorded audit.
    """
    if not isinstance(brief, CriticBrief):
        raise BriefError(
            f"run_critic takes a runner-built CriticBrief, got {type(brief).__name__}; "
            "briefs come from build_*_brief(), never from the run being audited"
        )
    from claude_agent_sdk import ClaudeAgentOptions, query

    stem = CRITIC_PROMPTS.get(brief.critic, brief.critic)
    spec = load_runner_prompt(stem)
    chosen = model or spec["model"]
    kwargs: dict[str, Any] = dict(
        env=cli_environment(),
        system_prompt=spec["prompt"],
        model=None if chosen == "inherit" else chosen,
        allowed_tools=spec["tools"],
        max_turns=spec["maxTurns"],
        cwd=cwd or brief.read_scope,
        cli_path=cli_path or resolve_cli(),
        mcp_servers={},
        # Nothing is watching this to answer a prompt, and a critic that stalls
        # on a permission dialog looks exactly like one that found nothing. The
        # tool allowlist is the real restriction: no Write, no Edit.
        permission_mode="bypassPermissions",
    )
    if spec.get("effort"):
        kwargs["effort"] = spec["effort"]
    if max_usd is not None:
        kwargs["max_budget_usd"] = max_usd
    options = ClaudeAgentOptions(**kwargs)

    chunks: list[str] = []
    async for message in query(prompt=brief.text, options=options):
        for block in getattr(message, "content", None) or []:
            text = getattr(block, "text", None)
            if text:
                chunks.append(text)
    return extract_json("\n".join(chunks))


# ── orchestration ─────────────────────────────────────────────────────────────


@dataclass
class CycleResult:
    verdict: dict[str, Any]
    reports: list[Any] = field(default_factory=list)
    plan: Any = None
    verdict_path: Path | None = None
