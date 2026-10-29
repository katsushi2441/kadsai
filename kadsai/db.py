import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_campaign (
  date TEXT, customer_id TEXT, campaign_id INTEGER, campaign_name TEXT, status TEXT, channel TEXT,
  budget REAL, impressions INTEGER, clicks INTEGER, cost REAL, conversions REAL, conv_value REAL,
  search_is REAL, lost_is_budget REAL, lost_is_rank REAL,
  PRIMARY KEY (date, customer_id, campaign_id));
CREATE TABLE IF NOT EXISTS daily_adgroup (
  date TEXT, customer_id TEXT, campaign_id INTEGER, adgroup_id INTEGER, adgroup_name TEXT, status TEXT,
  impressions INTEGER, clicks INTEGER, cost REAL, conversions REAL,
  PRIMARY KEY (date, customer_id, adgroup_id));
CREATE TABLE IF NOT EXISTS daily_keyword (
  date TEXT, customer_id TEXT, campaign_id INTEGER, adgroup_id INTEGER, criterion_id INTEGER,
  keyword TEXT, match_type TEXT, status TEXT, quality_score INTEGER,
  first_page_cpc REAL, top_page_cpc REAL, cpc_bid REAL,
  impressions INTEGER, clicks INTEGER, cost REAL, conversions REAL,
  PRIMARY KEY (date, customer_id, adgroup_id, criterion_id));
CREATE TABLE IF NOT EXISTS daily_search_term (
  date TEXT, customer_id TEXT, campaign_id INTEGER, adgroup_id INTEGER, search_term TEXT, status TEXT,
  impressions INTEGER, clicks INTEGER, cost REAL, conversions REAL,
  PRIMARY KEY (date, customer_id, adgroup_id, search_term));
CREATE TABLE IF NOT EXISTS daily_ad (
  date TEXT, customer_id TEXT, campaign_id INTEGER, adgroup_id INTEGER, ad_id INTEGER, ad_type TEXT,
  status TEXT, headline TEXT, final_url TEXT,
  impressions INTEGER, clicks INTEGER, cost REAL, conversions REAL,
  PRIMARY KEY (date, customer_id, ad_id));
CREATE TABLE IF NOT EXISTS daily_hour (
  date TEXT, customer_id TEXT, campaign_id INTEGER, hour INTEGER,
  impressions INTEGER, clicks INTEGER, cost REAL, conversions REAL,
  PRIMARY KEY (date, customer_id, campaign_id, hour));
CREATE TABLE IF NOT EXISTS daily_geo (
  date TEXT, customer_id TEXT, campaign_id INTEGER, geo_id INTEGER, geo_name TEXT,
  impressions INTEGER, clicks INTEGER, cost REAL, conversions REAL,
  PRIMARY KEY (date, customer_id, campaign_id, geo_id));
CREATE TABLE IF NOT EXISTS negative_keyword (
  customer_id TEXT, level TEXT, campaign_id INTEGER, adgroup_id INTEGER, criterion_id INTEGER,
  keyword TEXT, match_type TEXT, list_name TEXT,
  PRIMARY KEY (customer_id, level, campaign_id, adgroup_id, criterion_id));
CREATE TABLE IF NOT EXISTS fetch_log (
  fetched_at TEXT, customer_id TEXT, date_from TEXT, date_to TEXT, source TEXT, rows TEXT);
"""


def connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def upsert(con, table, rows):
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    con.executemany(sql, [[r[c] for c in cols] for r in rows])
    return len(rows)


def q(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def one(con, sql, params=()):
    r = con.execute(sql, params).fetchone()
    return dict(r) if r else None
