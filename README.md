# Kurage 広告AI分析 kadsai

Google 広告の実績を毎朝取り込み、規則で分析して日報を出すシステムです。AI は任意の1段で、無くても動きます。設定変更は人が承認したものだけ実行します。

- 取り込み: Google Ads API（口座の閲覧権限と API 設定が必要）。API が使えない口座は管理画面の CSV を取り込めます。
- 分析（規則）: 急な増減（同じ曜日の過去4週平均と比較）・成果ゼロの検索語（除外候補）・語単位の無駄（n-gram）・除外キーワードの衝突・予算負けと順位負け（インプレッションシェア）・入札が1ページ目に届かないキーワード・低い品質スコア・弱い広告文・成果の出ない時間帯・地域別。
- 日報: Markdown・HTML・短文（LINE 用）の3つを `outputs/reports/<日付>/` に書きます。
- AI 要約（任意）: `claude -p`・`codex exec` などのコマンド、または OpenAI 互換 API（DeepSeek・Qwen・ローカル LLM）。数字は規則が作り、AI は言い換えだけをします。
- 通知（任意）: LINE Messaging API の push / broadcast（外向きだけなので公開サーバー不要）、メール。
- 承認制の変更: 除外キーワード追加・日予算の変更・キーワード停止・キャンペーン停止。提案を見て `--apply` したものだけ API で実行します。

## 動かす場所

社内の Linux 1台（公開不要）。Python 3.10 以上。依存は `google-ads` と `requests` だけです。

```
pip install -r requirements.txt
python3 -m kadsai init            # kadsai.json の雛形
cp examples/env.example .env      # 秘密は .env に（権限 600）
```

`kadsai.json` に `customer_id`（広告の口座 ID）と `google_ads_yaml`（Google Ads API の設定ファイル）を書きます。管理者アカウント経由なら `login_customer_id` も書きます。

## 使い方

```
python3 -m kadsai fetch --days 35          # 取り込み（既定は昨日までの35日）
python3 -m kadsai analyze                  # 規則で分析して日報（outputs/reports/<日付>/）
python3 -m kadsai analyze --ai             # AI 要約も付ける
python3 -m kadsai notify                   # 日報の短文を LINE・メールで送る
python3 -m kadsai run                      # 上の3つを一括（毎朝これ1つ）
python3 -m kadsai proposals                # 承認待ちの提案
python3 -m kadsai proposals --apply 1 2    # 承認して実行（--dry-run で試し）
python3 -m kadsai proposals --reject 3
python3 -m kadsai import 検索語句.csv --kind search_terms   # 管理画面の CSV
```

毎朝 7 時に動かす例は `examples/kadsai.cron`（cron）と `examples/kadsai.service` + `kadsai.timer`（systemd）です。

## 設定（kadsai.json）

```json
{
 "account_name": "口座の呼び名",
 "customer_id": "1234567890",
 "login_customer_id": "",
 "google_ads_yaml": "google-ads.yaml",
 "rules": {"anomaly_threshold": 0.5, "wasted_min_cost": 1000, "wasted_min_clicks": 5},
 "ai": {"enabled": true, "command": "claude -p"},
 "line": {"enabled": true, "channel_access_token_env": "LINE_CHANNEL_ACCESS_TOKEN", "to": []},
 "email": {"enabled": false}
}
```

- `rules` は閾値です。書かなければ既定値（`kadsai/config.py` の DEFAULT_RULES）。
- `ai.command` は標準入力に日報を渡して標準出力を読みます。OpenAI 互換 API なら `openai_base_url`・`openai_model`・`openai_api_key_env` を書きます。
- `line.to` が空なら broadcast（友だち全員）、ユーザー ID を書けば push。通知専用の公式アカウントを1つ作り、受け取る人だけが友だち追加する形を勧めます。
- 秘密（トークン・API キー）は設定ファイルに書かず、環境変数名だけを書きます。

## 日報の中身

1. 要点（AI。設定したときだけ）
2. 対象日と直近7日の実績（費用・クリック・クリック単価・コンバージョン・1件あたり費用。前の7日との比較）
3. キャンペーン別
4. 指摘（重要・注意・参考の3段階。それぞれに「対応」を1行）
5. 成果が出た検索語、成果ゼロの検索語（除外候補）、時間帯別、地域別

## 変更は承認制

分析が出した提案は `outputs/proposals/proposals.json` にたまり、`proposals --apply` で承認したものだけ Google Ads API で実行します。自動では何も変えません。

## 作り

```
kadsai/
  fetch.py      Google Ads API → SQLite（読むだけ）
  csvimport.py  管理画面の CSV → SQLite
  rules.py      規則による分析（数字はここで確定）
  report.py     Markdown / HTML / 短文
  ai.py         AI 要約（コマンド or OpenAI 互換 API）
  notify.py     LINE / メール
  proposals.py  承認制の設定変更（ここだけが API で書く）
  cli.py        コマンド
```

データは `outputs/kadsai.db`（SQLite）1つ。日別のキャンペーン・広告グループ・キーワード・検索語・広告・時間帯・地域・除外キーワードの表です。

## ライセンス

MIT。`LICENSE` を見てください。
