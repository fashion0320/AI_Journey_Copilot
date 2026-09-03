"""全局配置 —— 通过 pydantic-settings 从 .env / 环境变量读取。"""

from __future__ import annotations

from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 高德 ----
    amap_key: str = Field(default="", alias="AMAP_KEY")
    amap_base_url: str = "https://restapi.amap.com"

    # ---- 豆包语音（火山引擎，v3 双向流式） ----
    volcengine_api_key: str = Field(default="", alias="VOLCENGINE_API_KEY")
    # ASR
    volcengine_asr_api_key: str = Field(default="", alias="VOLCENGINE_ASR_API_KEY")
    volcengine_asr_uri: str = Field(
        default="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
        alias="VOLCENGINE_ASR_URI",
    )
    volcengine_asr_resource_id: str = Field(
        default="volc.seedasr.sauc.duration",  # 豆包流式识别大模型 2.0 小时版
        alias="VOLCENGINE_ASR_RESOURCE_ID",
    )
    # TTS
    volcengine_tts_api_key: str = Field(default="", alias="VOLCENGINE_TTS_API_KEY")
    volcengine_tts_uri: str = Field(
        default="wss://openspeech.bytedance.com/api/v3/tts/bidirection",
        alias="VOLCENGINE_TTS_URI",
    )
    volcengine_tts_resource_id: str = Field(
        default="seed-tts-2.0",  # 豆包语音合成大模型 2.0
        alias="VOLCENGINE_TTS_RESOURCE_ID",
    )
    volcengine_tts_speaker: str = Field(
        default="zh_female_shuangkuai_moon_bigtts",  # 默认音色（需从控制台音色库获取正确 ID）
        alias="VOLCENGINE_TTS_SPEAKER",
    )

    # ---- Claude ----
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(
        default="claude-sonnet-5", alias="CLAUDE_MODEL"
    )
    claude_max_tokens: int = Field(default=4096, alias="CLAUDE_MAX_TOKENS")
    claude_temperature: float = Field(default=0.7, alias="CLAUDE_TEMPERATURE")

    # ---- Web Search（Claude 内置） ----
    web_search_allowed_domains: str = Field(
        default="", alias="WEB_SEARCH_ALLOWED_DOMAINS"
    )  # 逗号分隔，空=不限制
    web_search_blocked_domains: str = Field(
        default="", alias="WEB_SEARCH_BLOCKED_DOMAINS"
    )  # 逗号分隔
    web_search_max_uses: int = Field(default=5, alias="WEB_SEARCH_MAX_USES")

    # ---- Tavily Web Search ----
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    tavily_base_url: str = Field(
        default="https://api.tavily.com", alias="TAVILY_BASE_URL"
    )
    tavily_max_results: int = Field(default=10, alias="TAVILY_MAX_RESULTS")
    tavily_search_depth: str = Field(default="basic", alias="TAVILY_SEARCH_DEPTH")  # basic | advanced

    @property
    def web_search_allowed_list(self) -> list[str]:
        return [d.strip() for d in self.web_search_allowed_domains.split(",") if d.strip()]

    @property
    def web_search_blocked_list(self) -> list[str]:
        return [d.strip() for d in self.web_search_blocked_domains.split(",") if d.strip()]

    # ---- 服务 ----
    backend_host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ---- Demo 模拟模式 ----
    journey_demo_simulation: bool = Field(default=False, alias="JOURNEY_DEMO_SIMULATION")
    demo_eta_interval_sec: int = Field(default=10, alias="DEMO_ETA_INTERVAL_SEC")
    demo_eta_step_min: int = Field(default=2, alias="DEMO_ETA_STEP_MIN")

    # ---- 生产/开发模式 ----
    debug: bool = Field(default=False, alias="DEBUG")
    enable_test_routers: bool = Field(default=False, alias="ENABLE_TEST_ROUTERS")

    # ---- 日志 ----
    log_level: str = Field(default="info", alias="LOG_LEVEL")


settings = Settings()
