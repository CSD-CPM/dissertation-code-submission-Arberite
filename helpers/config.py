from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]

@dataclass
class Config:
    data: dict

    # GLOBAL FALLBACKS
    def model(self) -> str:
        return self.data.get("global", {}).get("model", "gpt-4o")

    def temp(self) -> float:
        return float(self.data.get("global", {}).get("temperature", 0.2))

    # AGENT-SPECIFIC VALUES
    def model_for(self, agent: str) -> str:
        """Return the model for a specific agent, or fallback to global."""
        models = self.data.get("models", {})
        global_model = self.data.get("global", {}).get("model", "gpt-4o")
        return models.get(agent, global_model)

    def temp_for(self, agent: str) -> float:
        """Return temperature for a specific agent, or fallback to global."""
        temps = self.data.get("temperatures", {})
        global_temp = float(self.data.get("global", {}).get("temperature", 0.2))
        return float(temps.get(agent, global_temp))

    # PATHS
    def path(self, key: str) -> str:
        return self.data.get("paths", {}).get(key, "")

    # PROMPTS
    def prompt(self, section: str, key: str) -> str:
        return self.data.get("prompts", {}).get(section, {}).get(key, "")

# FACTORY FUNCTION
def load_config() -> Config:
    cfg_path = PROJECT_ROOT / "config" / "config.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(data)