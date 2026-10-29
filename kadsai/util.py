import datetime as dt
import json
import os
import re

JST = dt.timezone(dt.timedelta(hours=9))


def today_jst():
    return dt.datetime.now(JST).date()


def parse_date(s):
    if s in (None, "", "yesterday"):
        return today_jst() - dt.timedelta(days=1)
    if s == "today":
        return today_jst()
    return dt.date.fromisoformat(s)


def ymd(d):
    return d.strftime("%Y-%m-%d")


def micros(v):
    return (v or 0) / 1_000_000


def fmt_money(v, unit="円"):
    return f"{v:,.0f}{unit}"


def fmt_pct(v):
    return f"{v * 100:.1f}%"


def ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p


def dump_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def tokens(term):
    """検索語を語に分ける。Google は語をスペースで区切って返す。区切りが無い語はそのまま1語。"""
    return [t for t in re.split(r"[\s　]+", term.strip()) if t]


def ngrams(term, n_max=3):
    ws = tokens(term)
    out = []
    for n in range(1, n_max + 1):
        for i in range(len(ws) - n + 1):
            out.append(" ".join(ws[i:i + n]))
    return out
