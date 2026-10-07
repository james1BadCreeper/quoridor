#!/bin/bash
# 运行 AI 容器：从 stdin 读入棋谱/选牌 json，向 stdout 输出一行决策 json。
# 沙箱限制：无网络、256MB 内存、单次运行随调随建随删。
# 用法：./run.sh [example|random] < kifu.json  （默认 example）
WHICH="${1:-example}"
if [ "$WHICH" = "random" ]; then IMG="quoridor-ai-random"; else IMG="quoridor-ai-example"; fi
exec docker run --rm -i --network none --memory=256m --pids-limit=64 "$IMG"
