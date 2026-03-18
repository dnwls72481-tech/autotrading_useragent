"""
Agent 환경변수 설정
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Agent 설정"""

    # 거래소 설정
    exchange: str = "bybit"
    api_key: str
    api_secret: str
    api_passphrase: str = ""      # OKX 전용

    # Agent 인증
    agent_token: str              # 앱에서 발급받은 토큰 (HMAC 서명 키 + Bearer 토큰)
    agent_user_id: str            # 앱 프로필에서 확인한 UUID

    # 중앙 서버 연결
    central_url: str              # e.g. https://central-server.railway.app

    # 서버 포트 (Railway 자동 제공)
    port: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


settings = AgentSettings()
