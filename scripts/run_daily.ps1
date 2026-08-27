# 毎朝の候補生成をローカルPCで実行し、結果をGitHubへ push する。
# タスクスケジューラから呼ばれる想定。手動でも実行できる。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_daily.ps1
#
# GitHub Actions を使わないのは、楽天APIが登録済みIPからのリクエストしか
# 受け付けず、Actionsのランナーが毎回別IPになるため（403 CLIENT_IP_NOT_ALLOWED）。

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "local\logs"
$logFile = Join-Path $logDir ("{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    Write-Output $line
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

Write-Log "=== 開始 ==="
Set-Location $root

if (-not (Test-Path $python)) {
    Write-Log "ERROR: 仮想環境が見つかりません: $python"
    Write-Log "  uv venv --python 3.12 .venv を実行してください。"
    exit 1
}

# --- 候補生成 ---------------------------------------------------------------
Write-Log "候補を生成します..."
& $python -m room.pipeline 2>&1 | ForEach-Object { Write-Log $_ }
$pipelineExit = $LASTEXITCODE

if ($pipelineExit -ne 0) {
    Write-Log "ERROR: 生成に失敗しました (exit $pipelineExit)"
    Write-Log "  403 CLIENT_IP_NOT_ALLOWED が出ている場合は、回線のIPが変わっています。"
    Write-Log "  https://webservice.rakuten.co.jp/app/list でIPを登録し直してください。"
    exit $pipelineExit
}

# --- GitHubへ反映 -----------------------------------------------------------
# docs/ と data/ だけを対象にする。設定やコードの編集中の変更を巻き込まないため。
Write-Log "GitHubへ反映します..."
git add docs data
$staged = git diff --staged --name-only
if (-not $staged) {
    Write-Log "変更がないためコミットはしません。"
} else {
    $message = "候補生成 {0}" -f (Get-Date -Format "yyyy-MM-dd")
    git commit -q -m $message
    if (-not $?) { Write-Log "ERROR: コミットに失敗しました"; exit 1 }

    git push -q origin main
    if (-not $?) {
        Write-Log "ERROR: push に失敗しました。ネットワークかGitHub認証を確認してください。"
        exit 1
    }
    Write-Log "push しました: $message"
    Write-Log "  https://iroha0813.github.io/rakuten-room/ に数分で反映されます。"
}

Write-Log "=== 完了 ==="
exit 0
