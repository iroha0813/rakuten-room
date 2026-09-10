#!/bin/bash
# 毎朝の候補生成をOracle Cloud VPS上で実行し、結果をGitHubへ push する。
# cron から呼ばれる想定。手動でも実行できる。
#
#   bash scripts/run_daily.sh
#
# GitHub Actions を使わないのは、楽天APIが登録済みIPからのリクエストしか
# 受け付けず、Actionsのランナーが毎回別IPになるため（403 CLIENT_IP_NOT_ALLOWED）。
# 固定IPを持つこのVPSで実行することで、IP登録が不要になる。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
LOG_DIR="$ROOT/local/logs"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date +%H:%M:%S)] $1" | tee -a "$LOG_FILE"
}

log "=== 開始 ==="
cd "$ROOT"

if [ ! -x "$PYTHON" ]; then
    log "ERROR: 仮想環境が見つかりません: $PYTHON"
    log "  python3.9 -m venv .venv を実行してください。"
    exit 1
fi

# --- 候補生成 ---------------------------------------------------------------
log "候補を生成します..."
"$PYTHON" -m room.pipeline 2>&1 | tee -a "$LOG_FILE"
PIPELINE_EXIT=${PIPESTATUS[0]}

if [ "$PIPELINE_EXIT" -ne 0 ]; then
    log "ERROR: 生成に失敗しました (exit $PIPELINE_EXIT)"
    log "  403 CLIENT_IP_NOT_ALLOWED が出ている場合は、このVPSのIPが変わっています。"
    log "  https://webservice.rakuten.co.jp/app/list でIPを登録し直してください。"
    exit "$PIPELINE_EXIT"
fi

# --- GitHubへ反映 -----------------------------------------------------------
# docs/ と data/ だけを対象にする。設定やコードの編集中の変更を巻き込まないため。
log "GitHubへ反映します..."
git add docs data

if git diff --staged --quiet; then
    log "変更がないためコミットはしません。"
else
    MESSAGE="候補生成 $(date +%Y-%m-%d)"
    if ! git commit -q -m "$MESSAGE"; then
        log "ERROR: コミットに失敗しました"
        exit 1
    fi

    if ! GIT_SSH_COMMAND="ssh -i $HOME/.ssh/github_deploy_key -o IdentitiesOnly=yes" git push -q origin main; then
        log "ERROR: push に失敗しました。ネットワークかGitHub認証を確認してください。"
        exit 1
    fi
    log "push しました: $MESSAGE"
    log "  https://iroha0813.github.io/rakuten-room/ に数分で反映されます。"
fi

log "=== 完了 ==="
exit 0
