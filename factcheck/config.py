import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(__file__).resolve().parent.parent / ".env") -> None:
    """Load KEY=VALUE lines from the repo-root .env. Variables already set in the environment win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _mock_default() -> bool:
    flag = _env("FC_MOCK")
    if flag in ("0", "1"):
        return flag == "1"
    # Without keys there is nothing to call, so fall back to the offline demo.
    return not (_env("CRUSOE_API_KEY") and _env("YDC_API_KEY"))


@dataclass
class Config:
    crusoe_api_key: str = field(default_factory=lambda: _env("CRUSOE_API_KEY"))
    crusoe_base_url: str = field(
        default_factory=lambda: _env("CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1")
    )
    ydc_api_key: str = field(default_factory=lambda: _env("YDC_API_KEY"))
    ydc_url: str = field(default_factory=lambda: _env("YDC_URL", "https://ydc-index.io/v1/search"))
    # Strong model: planning, judging, verifying. Fast model: high-volume sub-agent chatter.
    model_strong: str = field(
        default_factory=lambda: _env("FC_MODEL_STRONG", "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B")
    )
    model_fast: str = field(
        default_factory=lambda: _env("FC_MODEL_FAST", "nvidia/Nemotron-3.5-Lightning-30B-A3B")
    )
    # Concurrent LLM calls. Crusoe allows 30 RPM per model without a payment method.
    max_concurrency: int = field(default_factory=lambda: int(_env("FC_CONCURRENCY", "4")))
    max_tokens: int = field(default_factory=lambda: int(_env("FC_MAX_TOKENS", "4096")))
    mock: bool = field(default_factory=_mock_default)
    playbook: str = field(default_factory=lambda: _env("FC_PLAYBOOK", "playbooks/current.json"))
