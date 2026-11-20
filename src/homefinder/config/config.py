import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

CONFIG_DIR = Path(__file__).resolve().parent
ROOT = CONFIG_DIR.parents[2]  # repo root: config -> homefinder -> src -> repo
RULEBOOK_PATH = CONFIG_DIR / "rulebook.toml"

load_dotenv(ROOT / ".env")
SEARCH_SOURCE = os.getenv("SEARCH_SOURCE", "sample")  # sample or live
SAMPLE_PATH = ROOT / "tavily_sample" / "tavily_ans.json"
DB_PATH = Path(os.getenv("DB_PATH") or ROOT / "data" / "homefinder.db")  # SQLite documents store
RUN_LOG_PATH = ROOT / "data" / "run_log.jsonl"  # one JSON line per UI run: calls, tokens, seconds
CITY_COSTS_PATH = ROOT / "data" / "livingcost_india_all_inr.csv"
SAMPLE_CITY = "Chennai"  # the saved Tavily sample only covers this dev/test city
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("MODEL", "gemini-3.5-flash-lite")


def require_keys() -> list[str]:
    """Names of required .env settings that are missing (empty list = all set)."""
    required = {"GEMINI_API_KEY": GEMINI_API_KEY, "MODEL": MODEL}
    if SEARCH_SOURCE == "live":
        required["TAVILY_API_KEY"] = os.getenv("TAVILY_API_KEY")
    return [name for name, value in required.items() if not value]


def sample_supports(city: str) -> bool:
    """True if sample mode can serve this city ("Chennai, Tamil Nadu" counts as Chennai)."""
    return city.split(",")[0].strip().lower() == SAMPLE_CITY.lower()


def load_rulebook(path: Path = RULEBOOK_PATH) -> dict:
    with open(path, "rb") as f:
        rb = tomllib.load(f)
    total = sum(rb["weights"].values())
    if abs(total - 100) > 1e-9:
        raise ValueError(f"rulebook [weights] must sum to 100, got {total}")
    return rb
