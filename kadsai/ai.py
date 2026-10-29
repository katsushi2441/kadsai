"""AI 要約。差し替え式。無ければ何もしない。数字は日報の中のものだけを使わせる。"""
import json
import os
import subprocess

import requests

PROMPT = """あなたは広告運用の担当者です。次の広告日報を、忙しい経営者向けに日本語でまとめてください。

出す形:
1行目: 昨日と直近の調子を一言（良い／ふつう／悪い のどれかと理由）
2〜4行目: 要点を3つ（1行ずつ、数字は日報にあるものだけ）
最後の行: 「今日やること: 」に続けて、いちばん効く対応を1つ

決まり: 日報に無い数字や事実を作らない。専門用語は避ける。全体で200字以内。見出しや記号（#・*）は使わない。

--- 日報 ---
"""


def summarize(cfg, report_md, force=False):
    ai = cfg.ai
    if not ai or (not ai.get("enabled") and not force):
        return None
    text = PROMPT + report_md[: int(ai.get("max_chars", 12000))]
    timeout = int(ai.get("timeout", 300))
    if ai.get("openai_base_url"):
        key = os.environ.get(ai.get("openai_api_key_env", "OPENAI_API_KEY"), "")
        r = requests.post(ai["openai_base_url"].rstrip("/") + "/chat/completions",
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          json={"model": ai.get("openai_model", ""), "messages": [{"role": "user", "content": text}],
                                "temperature": 0.2, "max_tokens": 600}, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    cmd = ai.get("command")
    if cmd:
        p = subprocess.run(cmd, shell=True, input=text, capture_output=True, text=True, timeout=timeout, cwd=cfg.outputs)
        if p.returncode != 0:
            raise RuntimeError(f"AI command failed ({p.returncode}): {p.stderr[-500:]}")
        return p.stdout.strip()
    return None
