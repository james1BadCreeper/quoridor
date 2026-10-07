#!/bin/bash
# 构建两个示例 AI 镜像（容器内编译）
set -e
cd "$(dirname "$0")"
docker build -t quoridor-ai-example -f Dockerfile .
docker build -t quoridor-ai-random -f Dockerfile.random .
echo '--- 构建成功，自检选牌阶段 ---'
echo '{"phase":"select","skill_k":2}' | ./run.sh example
echo '{"phase":"select","skill_k":3}' | ./run.sh random
