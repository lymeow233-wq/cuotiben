#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}

if [ -n "${OPENAI_API_KEY:-}" ]; then
  echo "模型：已配置（${OPENAI_MODEL:-gpt-4o-mini} @ ${OPENAI_BASE_URL:-api.openai.com}）"
else
  echo "模型：未配置 OPENAI_API_KEY → 文本走关键词规则打标，照片走 OCR 兜底"
fi

if command -v tesseract >/dev/null 2>&1; then
  command -v tesseract | xargs -I{} sh -c 'tesseract --list-langs 2>/dev/null | grep -q chi_sim' \
    && echo "OCR：tesseract + chi_sim 就绪" \
    || echo "OCR：缺 chi_sim 语言包（照片兜底会不可用）"
else
  echo "OCR：未安装 tesseract（仅影响照片兜底）"
fi

exec "$PY" server.py
