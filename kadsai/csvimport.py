"""Google 広告の管理画面から出した CSV を取り込む。API が使えない口座（代理店運用）向け。
管理画面のレポートは先頭に題名行があり、列名は日本語・英語どちらもある。"""
import csv
import datetime as dt
import hashlib
import io
import json
import zlib

from . import db

ALIASES = {
    "date": ["日", "日付", "Day", "Date"],
    "campaign": ["キャンペーン", "Campaign"],
    "adgroup": ["広告グループ", "Ad group"],
    "search_term": ["検索語句", "検索語", "Search term"],
    "keyword": ["キーワード", "Keyword", "検索キーワード"],
    "match_type": ["マッチタイプ", "Match type"],
    "impressions": ["表示回数", "インプレッション", "Impr.", "Impressions"],
    "clicks": ["クリック数", "クリック", "Clicks"],
    "cost": ["費用", "Cost"],
    "conversions": ["コンバージョン", "コンバージョン数", "Conversions", "Conv."],
    "status": ["ステータス", "追加/除外", "Added/Excluded", "Status"],
    "hour": ["時間", "Hour of day", "時間帯"],
}


def _id(name):
    """名前から安定した ID を作る（CSV には ID が無い）。負の数にして API の ID と混ざらないようにする。"""
    return -(zlib.crc32(name.encode("utf-8")) & 0x7FFFFFFF)


def _num(v):
    if v in (None, "", "--", " --"):
        return 0.0
    s = str(v).replace(",", "").replace("¥", "").replace("円", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _date(v):
    s = str(v).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y年%m月%d日", "%m-%d-%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"日付が読めません: {v}")


def _read(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("文字コードが読めません（UTF-8 / UTF-16 / Shift_JIS）")
    lines = text.splitlines()
    # 先頭の題名行（列名を含まない行）を飛ばす
    start = 0
    for i, l in enumerate(lines[:10]):
        if any(a in l for a in ALIASES["impressions"] + ALIASES["clicks"]):
            start = i
            break
    dialect = "excel-tab" if "\t" in lines[start] else "excel"
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:])), dialect=dialect))
    # 末尾の「合計」行を落とす
    return [r for r in rows if not any(str(v).startswith(("合計", "Total")) for v in r.values() if v)]


def _col(row, key):
    for a in ALIASES[key]:
        if a in row:
            return row[a]
    return None


def import_csv(cfg, path, kind, date=None):
    rows = _read(path)
    con = db.connect(cfg.db)
    cid = cfg.customer_id
    out = []
    for r in rows:
        d = _col(r, "date")
        d = _date(d) if d else (date or dt.date.today().isoformat())
        camp = _col(r, "campaign") or "(キャンペーン)"
        ag = _col(r, "adgroup") or "(広告グループ)"
        base = dict(date=d, customer_id=cid, campaign_id=_id(camp), impressions=int(_num(_col(r, "impressions"))),
                    clicks=int(_num(_col(r, "clicks"))), cost=_num(_col(r, "cost")), conversions=_num(_col(r, "conversions")))
        if kind == "search_terms":
            base.update(adgroup_id=_id(camp + "/" + ag), search_term=(_col(r, "search_term") or "").strip(),
                        status=_col(r, "status") or "")
            if not base["search_term"]:
                continue
        elif kind == "campaign":
            base.update(campaign_name=camp, status=_col(r, "status") or "ENABLED", channel="SEARCH", budget=0,
                        conv_value=0, search_is=0, lost_is_budget=0, lost_is_rank=0)
        elif kind == "keyword":
            kw = (_col(r, "keyword") or "").strip()
            base.update(adgroup_id=_id(camp + "/" + ag), criterion_id=_id(kw + "|" + (_col(r, "match_type") or "")),
                        keyword=kw, match_type=_col(r, "match_type") or "", status="ENABLED", quality_score=None,
                        first_page_cpc=0, top_page_cpc=0, cpc_bid=0)
        elif kind == "adgroup":
            base.update(adgroup_id=_id(camp + "/" + ag), adgroup_name=ag, status="ENABLED")
        else:
            raise ValueError("kind は search_terms / campaign / keyword / adgroup")
        out.append(base)
    table = {"search_terms": "daily_search_term", "campaign": "daily_campaign", "keyword": "daily_keyword",
             "adgroup": "daily_adgroup"}[kind]
    n = db.upsert(con, table, out)
    if kind == "search_terms" or kind == "keyword":
        # キャンペーン名の表は campaign CSV が無くても埋める
        names = {}
        for r in rows:
            names[_id(_col(r, "campaign") or "(キャンペーン)")] = _col(r, "campaign") or "(キャンペーン)"
        for c_id, name in names.items():
            if not db.one(con, "SELECT 1 FROM daily_campaign WHERE customer_id=? AND campaign_id=?", (cid, c_id)):
                db.upsert(con, "daily_campaign", [dict(date=out[0]["date"] if out else date, customer_id=cid, campaign_id=c_id,
                                                      campaign_name=name, status="ENABLED", channel="SEARCH", budget=0,
                                                      impressions=0, clicks=0, cost=0, conversions=0, conv_value=0,
                                                      search_is=0, lost_is_budget=0, lost_is_rank=0)])
    con.execute("INSERT INTO fetch_log VALUES (?,?,?,?,?,?)",
                (dt.datetime.now().isoformat(timespec="seconds"), cid, "", "", f"csv:{kind}:{path}", json.dumps({kind: n})))
    con.commit()
    con.close()
    return n
