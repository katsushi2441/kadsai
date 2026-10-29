import os

from .util import load_json

DEFAULT_RULES = {
    "anomaly_weeks": 4,          # 同じ曜日を何週さかのぼって平均するか
    "anomaly_threshold": 0.5,    # 平均との差がこの割合を超えたら異常（0.5=±50%）
    "anomaly_min_cost": 500,     # 費用がこれ未満の日は異常と言わない
    "anomaly_min_clicks": 10,
    "search_term_days": 30,      # 検索語の集計期間
    "wasted_min_cost": 1000,     # 成果ゼロでこの費用を超えた検索語は除外候補
    "wasted_min_clicks": 5,      # 成果ゼロでこのクリックを超えた検索語は除外候補
    "ngram_min_cost": 2000,      # 語（n-gram）単位で成果ゼロのまま超えた費用
    "is_days": 7,                # インプレッションシェアの集計期間
    "is_lost_budget": 0.2,       # 予算負けがこの割合を超えたら指摘
    "is_lost_rank": 0.3,         # 順位負けがこの割合を超えたら指摘
    "quality_score_low": 4,
    "report_days": 7,            # 日報の「直近7日 vs その前7日」
    "ad_min_impressions": 100,   # 広告文の比較に必要な表示回数
    "hour_days": 30,             # 時間帯分析の期間
}


class Config:
    def __init__(self, path):
        self.path = path
        self.raw = load_json(path)
        self.base = os.path.dirname(os.path.abspath(path))
        self.customer_id = str(self.raw.get("customer_id", "")).replace("-", "")
        self.login_customer_id = str(self.raw.get("login_customer_id", "") or "").replace("-", "") or None
        self.google_ads_yaml = self._abs(self.raw.get("google_ads_yaml", "google-ads.yaml"))
        self.outputs = self._abs(self.raw.get("outputs", "outputs"))
        self.db = self._abs(self.raw.get("db", os.path.join(self.outputs, "kadsai.db")))
        self.currency = self.raw.get("currency", "円")
        self.account_name = self.raw.get("account_name", "")
        self.rules = dict(DEFAULT_RULES)
        self.rules.update(self.raw.get("rules", {}))
        self.ai = self.raw.get("ai", {}) or {}
        self.line = self.raw.get("line", {}) or {}
        self.email = self.raw.get("email", {}) or {}

    def _abs(self, p):
        if not p:
            return p
        return p if os.path.isabs(p) else os.path.join(self.base, p)

    def env(self, key_name, default=None):
        """設定に書いた環境変数名から値を読む。秘密は設定ファイルに直接書かない。"""
        name = self.raw.get(key_name) or default
        return os.environ.get(name) if name else None


EXAMPLE = {
    "account_name": "（口座の呼び名）",
    "customer_id": "1234567890",
    "login_customer_id": "",
    "google_ads_yaml": "google-ads.yaml",
    "outputs": "outputs",
    "currency": "円",
    "rules": DEFAULT_RULES,
    "ai": {
        "enabled": False,
        "command": "claude -p",
        "openai_base_url": "",
        "openai_model": "",
        "openai_api_key_env": "OPENAI_API_KEY",
        "timeout": 300,
    },
    "line": {
        "enabled": False,
        "channel_access_token_env": "LINE_CHANNEL_ACCESS_TOKEN",
        "to": [],
    },
    "email": {
        "enabled": False,
        "smtp_host": "",
        "smtp_port": 587,
        "user_env": "SMTP_USER",
        "password_env": "SMTP_PASSWORD",
        "from": "",
        "to": [],
    },
}
