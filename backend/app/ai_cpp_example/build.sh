#!/bin/bash
# 构建示例 AI 镜像（容器内编译）
set -e
cd "$(dirname "$0")"
docker build -t quoridor-ai-example .
echo '--- 构建成功，自检选牌阶段 ---'
echo '{"phase":"select","skill_k":2}' | ./run.sh
