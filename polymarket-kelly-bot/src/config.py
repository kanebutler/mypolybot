"""
Config Module - Configuration Management

Loads configuration from YAML files and environment variables.

Precedence (highest to lowest):
    1. Environment variables (POLY_*)
    2. YAML config file
    3. Default values

Example:
    from src.config import Config
    config = Config.from_env()
"""

import os
from pathlib import Path
from typing import Dict, Any, List
from dataclasses import dataclass, field, asdict
import yaml


ENV_PREFIX = "POLY_"


def get_env(name: str, default: str = "") -> str:
    return os.environ.get(f"{ENV_PREFIX}{name}", default)


def get_env_int(name: str, default: int = 0) -> int:
    val = get_env(name, "")
    try:
        return int(val) if val else default
    except ValueError:
        return default


def get_env_float(name: str, default: float = 0.0) -> float:
    val = get_env(name, "")
    try:
        return float(val) if val else default
    except ValueError:
        return default


class ConfigError(Exception):
    pass


@dataclass
class BuilderConfig:
    """Builder Program credentials for gasless transactions."""
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret and self.api_passphrase)


@dataclass
class ClobConfig:
    """CLOB API configuration."""
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    signature_type: int = 2  # Gnosis Safe


@dataclass
class RelayerConfig:
    """Relayer configuration for gasless transactions."""
    host: str = "https://relayer-v2.polymarket.com"
    tx_type: str = "SAFE"


@dataclass
class KellyConfig:
    """Kelly Criterion configuration."""
    fraction: float = 0.25       # 1/4 Kelly default
    min_edge: float = 0.01       # Minimum edge to trade
    max_fraction: float = 0.20   # Max bankroll fraction per trade
    min_bet: float = 1.0         # Minimum bet in USDC


@dataclass
class Config:
    """Main configuration for the trading bot."""

    safe_address: str = ""
    rpc_url: str = "https://polygon-rpc.com"

    clob: ClobConfig = field(default_factory=ClobConfig)
    relayer: RelayerConfig = field(default_factory=RelayerConfig)
    builder: BuilderConfig = field(default_factory=BuilderConfig)
    kelly: KellyConfig = field(default_factory=KellyConfig)

    default_token_id: str = ""
    default_size: float = 1.0
    data_dir: str = "credentials"
    log_level: str = "INFO"
    use_gasless: bool = False

    def __post_init__(self):
        if self.safe_address:
            self.safe_address = self.safe_address.lower()
        if self.builder.is_configured():
            self.use_gasless = True

    @classmethod
    def load(cls, filepath: str = "config.yaml") -> "Config":
        path = Path(filepath)
        if not path.exists():
            raise ConfigError(f"Config file not found: {filepath}")
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        config = cls()
        if "safe_address" in data:
            config.safe_address = data["safe_address"]
        if "rpc_url" in data:
            config.rpc_url = data["rpc_url"]
        if "clob" in data:
            d = data["clob"]
            config.clob = ClobConfig(
                host=d.get("host", config.clob.host),
                chain_id=d.get("chain_id", config.clob.chain_id),
                signature_type=d.get("signature_type", config.clob.signature_type),
            )
        if "relayer" in data:
            d = data["relayer"]
            config.relayer = RelayerConfig(
                host=d.get("host", config.relayer.host),
                tx_type=d.get("tx_type", config.relayer.tx_type),
            )
        if "builder" in data:
            d = data["builder"]
            config.builder = BuilderConfig(
                api_key=d.get("api_key", ""),
                api_secret=d.get("api_secret", ""),
                api_passphrase=d.get("api_passphrase", ""),
            )
        if "kelly" in data:
            d = data["kelly"]
            config.kelly = KellyConfig(
                fraction=d.get("fraction", config.kelly.fraction),
                min_edge=d.get("min_edge", config.kelly.min_edge),
                max_fraction=d.get("max_fraction", config.kelly.max_fraction),
                min_bet=d.get("min_bet", config.kelly.min_bet),
            )
        for key in ("default_token_id", "data_dir", "log_level"):
            if key in data:
                setattr(config, key, data[key])
        if "default_size" in data:
            config.default_size = float(data["default_size"])
        config.use_gasless = config.builder.is_configured()
        return config

    @classmethod
    def from_env(cls) -> "Config":
        config = cls()
        sa = get_env("SAFE_ADDRESS")
        if sa:
            config.safe_address = sa
        rpc = get_env("RPC_URL")
        if rpc:
            config.rpc_url = rpc
        ak = get_env("BUILDER_API_KEY")
        ase = get_env("BUILDER_API_SECRET")
        ap = get_env("BUILDER_API_PASSPHRASE")
        if ak or ase or ap:
            config.builder = BuilderConfig(api_key=ak, api_secret=ase, api_passphrase=ap)
        ch = get_env("CLOB_HOST")
        if ch:
            config.clob.host = ch
        ci = get_env_int("CHAIN_ID", 137)
        if ci != 137:
            config.clob.chain_id = ci
        kf = get_env_float("KELLY_FRACTION")
        if kf:
            config.kelly.fraction = kf
        config.use_gasless = config.builder.is_configured()
        return config

    def to_dict(self) -> Dict[str, Any]:
        return {
            "safe_address": self.safe_address,
            "rpc_url": self.rpc_url,
            "clob": asdict(self.clob),
            "relayer": asdict(self.relayer),
            "builder": asdict(self.builder),
            "kelly": asdict(self.kelly),
            "default_token_id": self.default_token_id,
            "default_size": self.default_size,
            "data_dir": self.data_dir,
            "log_level": self.log_level,
        }

    def save(self, filepath: str = "config.yaml") -> None:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, indent=2)

    def validate(self) -> List[str]:
        errors = []
        if not self.safe_address:
            errors.append("safe_address is required")
        if self.use_gasless and not self.builder.is_configured():
            errors.append("gasless enabled but builder credentials missing")
        return errors
