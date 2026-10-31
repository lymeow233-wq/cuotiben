#!/usr/bin/env bash
# 一键同步：把本地改动提交并推送到 GitHub，GitHub Pages 随后自动部署
# 用法：
#   bash sync.sh "这次改了什么"
# 不加说明时自动用时间戳作为提交信息

set -e
cd "$(dirname "$0")"

MSG="${1:-"更新原型 $(date '+%Y-%m-%d %H:%M')"}"

# 只跟踪需要上线的文件，备份和临时文件由 .gitignore 排除
git add index.html m.html README.md .gitignore sync.sh

# 没有实质改动就退出
if git diff --cached --quiet; then
  echo "没有检测到改动，无需同步。"
  exit 0
fi

git -c core.quotepath=false commit -q -m "$MSG"

echo "正在推送到 GitHub ..."
git push

echo
echo "✅ 已推送：$MSG"
echo "   仓库：https://github.com/lymeow233-wq/cuotiben"
echo "   电脑：https://lymeow233-wq.github.io/cuotiben/"
echo "   手机：https://lymeow233-wq.github.io/cuotiben/m.html"
echo "   网页约 1 分钟后自动更新，链接不变。"
