"""
Model wiring shared by both eval phases.

Phase 1 drives the agent under test; phase 2 drives the judges. Both used to
construct `BedrockModel` inline, which made Bedrock the only reachable provider
even though strands ships `openai`, `litellm`, `ollama` and others, and the evals
SDK's judges accept a `Model` object as readily as a model-id string
(`SkillSelectionAccuracyEvaluator(model: Model | str | None)`).

This module is the one place that turns environment into a provider instance:

    MODEL_PROVIDER=bedrock          # default
    MODEL_PROVIDER=openai           # any OpenAI-compatible endpoint

`openai` covers far more than OpenAI itself -- vLLM, SGLang, llama.cpp's server,
Ollama's `/v1` shim, a LiteLLM proxy, OpenRouter -- because all of them speak the
same wire format. That is the whole reason to key on the *protocol* rather than
adding one provider per vendor.

The two roles are configured independently, agent first and judge falling back to
it:

    role    provider                                model id
    agent   MODEL_PROVIDER                          MODEL_ID (or BEDROCK_MODEL_ID)
    judge   JUDGE_MODEL_PROVIDER -> MODEL_PROVIDER  JUDGE_MODEL_ID

so `MODEL_PROVIDER=openai` moves both phases, while
`JUDGE_MODEL_PROVIDER=openai` moves only the judges. Re-scoring reuses the agent
recording but still makes judge calls and can vary with model sampling.

Model ids do not fall back across roles. The agent and the judge are different
models by design, and inheriting one id into the other role silently would score a
run with the wrong model while reporting the right one.

For OpenAI-compatible endpoints:

    OPENAI_BASE_URL=http://localhost:8000/v1     # omit to reach api.openai.com
    OPENAI_API_KEY=...                           # defaults to EMPTY when a
                                                 # base_url is set
    JUDGE_OPENAI_BASE_URL / JUDGE_OPENAI_API_KEY # optional per-role override

Two caveats that belong with the config rather than in a commit message:

* **The judges need tool calling, specifically.** Every judge asks for a pydantic
  model back (`structured_output_model=SkillSelectionRating`). On the OpenAI wire
  format strands sends that as a *tool* named after the model -- observed on the
  wire as `tools: [{function: {name: "SkillSelectionRating"}}]` with
  `tool_choice` unset and `response_format` unused. So json-schema / "JSON mode"
  support is not enough, and because the call is not forced, a model that answers
  in prose instead of calling the tool produces no parseable verdict. Neither
  failure reports itself as a config error: both surface as judges scoring 0.00,
  i.e. as a failing skill. Same shape as a malformed Bedrock profile id, and the
  same place to check first.
* **The agent under test needs reliable function calling.** It has to drive a
  Skill/Read/Write/Bash loop inside a 40-call budget. A model with weak tool use
  degrades into a retry loop, and then the score measures the model rather than
  the skill -- which also makes it incomparable to any Bedrock-recorded run.
"""

from __future__ import annotations

import os
from typing import Any

#: Bedrock wants a full inference-profile id, not a bare family name. See
#: `run_strands_eval.JUDGE_MODEL` for what a wrong one looks like from the report.
BEDROCK_AGENT_DEFAULT = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
BEDROCK_JUDGE_DEFAULT = "global.anthropic.claude-sonnet-4-6"

#: Bedrock's default output cap for Anthropic models is 4096 tokens. A skill that
#: hands the agent a whole spec JSON or .drawio to `Write` in one tool call needs
#: more than that; a stronger model, which writes longer specs, hit the cap and
#: failed with a truncated tool input where a smaller one had squeezed under it.
#: Override with `MODEL_MAX_TOKENS` / `JUDGE_MODEL_MAX_TOKENS`.
DEFAULT_MAX_TOKENS = 16384

#: vLLM and friends require the client to send *some* key and then ignore it. The
#: OpenAI SDK refuses to construct without one, so an unset key against a local
#: server would fail before a single request went out.
LOCAL_API_KEY_PLACEHOLDER = "EMPTY"


class ModelConfigError(RuntimeError):
    """Raised for provider config that cannot produce a usable model.

    Raised at construction, before any case runs. The alternative -- letting a
    misconfigured provider through -- turns a typo into a suite of zero scores
    that reads as a broken skill.
    """


def _first(*names: str, default: str | None = None) -> str | None:
    """First environment variable of `names` that is set and non-empty.

    Empty counts as unset on purpose: `MODEL_PROVIDER=` in a CI env block or a
    sourced `.env` is how a variable ends up defined-but-blank, and treating that
    as a provider named "" fails far from its cause.
    """
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def agent_provider() -> str:
    return (_first("MODEL_PROVIDER", default="bedrock") or "bedrock").lower()


def judge_provider() -> str:
    return (_first("JUDGE_MODEL_PROVIDER", "MODEL_PROVIDER", default="bedrock") or "bedrock").lower()


def agent_model_id() -> str:
    """`BEDROCK_MODEL_ID` is still honoured: it is the documented name and the one
    already in use, and silently ignoring it would move a run to the default model
    while the caller believed they had pinned one."""
    return _first("MODEL_ID", "BEDROCK_MODEL_ID", default=BEDROCK_AGENT_DEFAULT) or ""


def judge_model_id() -> str:
    return _first("JUDGE_MODEL_ID", default=BEDROCK_JUDGE_DEFAULT) or ""


def bedrock_region() -> str:
    """Which region the Bedrock provider talks to.

    Resolved rather than defaulted outright, because the two phases used to differ
    and centralising them here would otherwise silently move one. Phase 1 forced
    `AWS_REGION or "us-east-1"`, overriding a configured profile; phase 2 passed the
    model as a bare id string and so inherited the full boto chain, profile region
    included. Forcing us-east-1 on both would have relocated every profile-based
    phase-2 run without saying so -- and a judge that suddenly has no Bedrock access
    in the region it was moved to reports zeros, not a region error.

    So: explicit environment first, then the chain every other AWS tool on the box
    follows, then the kit's own default. The last step is deliberate too --
    strands' own fallback is us-west-2, and the judge default is a `global.` profile
    that wants a region that actually has it enabled.
    """
    explicit = _first("AWS_REGION", "AWS_DEFAULT_REGION")
    if explicit:
        return explicit
    try:
        import boto3

        session_region = boto3.Session().region_name
    except Exception:
        # No credentials file, no botocore, a malformed profile: none of that is
        # this function's problem to report. The call that follows will say so
        # properly.
        session_region = None
    return session_region or "us-east-1"


def max_tokens(role: str) -> int:
    prefix = "JUDGE_" if role == "judge" else ""
    return int(_first(f"{prefix}MODEL_MAX_TOKENS", "MODEL_MAX_TOKENS",
                      default=str(DEFAULT_MAX_TOKENS)) or DEFAULT_MAX_TOKENS)


def _bedrock(model_id: str, role: str) -> Any:
    from strands.models import BedrockModel

    return BedrockModel(model_id=model_id, region_name=bedrock_region(), max_tokens=max_tokens(role))


def _openai(model_id: str, role: str) -> Any:
    # Keep the optional OpenAI client out of the default Bedrock import path.
    try:
        from strands.models.openai import OpenAIModel
    except ImportError as e:
        raise ModelConfigError(
            "the openai provider needs the OpenAI client: "
            "configure MODEL_PROVIDER/JUDGE_MODEL_PROVIDER, then run `uv run skill-eval setup`"
        ) from e

    prefix = "JUDGE_" if role == "judge" else ""
    base_url = _first(f"{prefix}OPENAI_BASE_URL", "OPENAI_BASE_URL")
    api_key = _first(f"{prefix}OPENAI_API_KEY", "OPENAI_API_KEY")

    client_args: dict[str, Any] = {}
    if base_url:
        client_args["base_url"] = base_url
        # A self-hosted server almost never checks the key, so requiring one would
        # be friction with no security value. api.openai.com does check it, which
        # is why the placeholder is scoped to the base_url case.
        client_args["api_key"] = api_key or LOCAL_API_KEY_PLACEHOLDER
    elif api_key:
        client_args["api_key"] = api_key
    else:
        raise ModelConfigError(
            f"{role}: provider 'openai' needs OPENAI_BASE_URL (a self-hosted or "
            "proxied endpoint) or OPENAI_API_KEY (api.openai.com)"
        )

    return OpenAIModel(client_args=client_args, model_id=model_id,
                       params={"max_tokens": max_tokens(role)})


def build_model(role: str) -> Any:
    """A strands `Model` for `role` ("agent" or "judge").

    Returned as an object rather than an id string because that is the only form
    that can carry a provider. Both consumers accept one: `Agent(model=...)` and
    the SDK judges' `model=` parameter.
    """
    validate_config(role)

    provider = agent_provider() if role == "agent" else judge_provider()
    model_id = agent_model_id() if role == "agent" else judge_model_id()

    if provider == "bedrock":
        return _bedrock(model_id, role)
    if provider == "openai":
        # The defaults are Bedrock inference profiles. Handing one to a vLLM server
        # gets a 404 on an id that looks legitimate, so refuse instead: forgetting
        # the model id is the expected mistake when switching providers, and it is
        # only diagnosable here.
        if model_id in (BEDROCK_AGENT_DEFAULT, BEDROCK_JUDGE_DEFAULT):
            var = "MODEL_ID" if role == "agent" else "JUDGE_MODEL_ID"
            raise ModelConfigError(
                f"{role}: provider 'openai' with the default Bedrock model id "
                f"{model_id!r}. Set {var} to a model the endpoint serves "
                "(e.g. Qwen/Qwen3-32B)."
            )
        return _openai(model_id, role)

    raise ModelConfigError(
        f"{role}: unknown provider {provider!r}; supported: bedrock, openai. "
        "Any OpenAI-compatible server (vLLM, SGLang, llama.cpp, Ollama /v1, "
        "LiteLLM proxy, OpenRouter) uses 'openai' with OPENAI_BASE_URL."
    )


def validate_config(role: str) -> None:
    """Validate environment settings without constructing clients or using the network."""
    if role not in ("agent", "judge"):
        raise ValueError(f"role must be 'agent' or 'judge', got {role!r}")
    config = describe(role)
    provider, model_id = config["model_provider"], config["model_id"]
    if provider not in ("bedrock", "openai"):
        raise ModelConfigError(f"{role}: unsupported provider {provider!r}; use bedrock or openai")
    if provider == "openai":
        if model_id in (BEDROCK_AGENT_DEFAULT, BEDROCK_JUDGE_DEFAULT):
            raise ModelConfigError(f"{role}: set {'MODEL_ID' if role == 'agent' else 'JUDGE_MODEL_ID'} "
                                   "to a model served by your OpenAI-compatible endpoint")
        prefix = "JUDGE_" if role == "judge" else ""
        if not (_first(f"{prefix}OPENAI_BASE_URL", "OPENAI_BASE_URL")
                or _first(f"{prefix}OPENAI_API_KEY", "OPENAI_API_KEY")):
            raise ModelConfigError(f"{role}: set OPENAI_API_KEY or OPENAI_BASE_URL "
                                   "(JUDGE_ overrides are supported)")


def describe(role: str) -> dict[str, str]:
    """Provider and model id for the recording.

    Written into `results.json` so a run says which model produced it. A recording
    that names only the model id is ambiguous once more than one provider can serve
    the same name, and phase 2 re-scores that recording long after the environment
    that produced it is gone.
    """
    if role == "agent":
        return {"model_provider": agent_provider(), "model_id": agent_model_id()}
    return {"model_provider": judge_provider(), "model_id": judge_model_id()}
