#!/usr/bin/env bash
# デモ用: 取得 → 規則分析 → AI 要約 → 通知本文を表示（送信は --send を付けたときだけ）
set -euo pipefail
cd "$(dirname "$0")/.."
CFG="${CFG:-kadsai.json}"
DATE="${DATE:-yesterday}"
echo "== 1. Google 広告から取り込み"; /usr/bin/python3 -W ignore -m kadsai -c "$CFG" fetch --date "$DATE" --days 35
echo "== 2. 規則で分析 + AI 要約"; /usr/bin/python3 -W ignore -m kadsai -c "$CFG" analyze --date "$DATE" --ai | head -40
echo "== 3. 承認待ちの提案"; /usr/bin/python3 -W ignore -m kadsai -c "$CFG" proposals
if [ "${1:-}" = "--send" ]; then echo "== 4. 通知"; /usr/bin/python3 -W ignore -m kadsai -c "$CFG" notify --date "$DATE"; fi
