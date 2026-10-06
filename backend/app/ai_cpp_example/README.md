# AI 编写指南

本项目的最大功能是测试编写的 AI 的性能。本节指导你如何编写自己的 AI。

## 1. 棋谱 JSON 格式

通过 `GET /api/games/{id}/export` 可导出当前棋谱，结构如下：

```json
{
  "n": 9, "m": 9,
  "walls_total": 10,
  "pawns": [[0, 4], [8, 4]],
  "walls_left": [10, 10],
  "walls": [{"wr": 2, "wc": 3, "orientation": "H"}],
  "deads": [[4, 4]], "sands": [[5, 5]],
  "goal_A": [0, 1, 2, 3],
  "goal_B": [5, 6, 7, 8],
  "turn": 0, "bonus_moves": 0,
  "winner": null, "win_reason": null,
  "history": [{"player": 0, "type": "move", "to": [1, 4]}]
}
```

- 先手出生顶行、目标为底行中列 ∈ A；后手出生底行、目标为顶行中列 ∈ B。
- `turn` 为轮到行动方；`bonus_moves>0` 表示因流沙同一人继续行动。

## 2. AI 输入输出协议

打包好的 AI 是一个 `.zip` 文件，里面包含 cpp 代码，实现：

- 输入：当前棋谱的 json（通过 stdin 传入）。
- 输出：决策（一行 json，通过 stdout 输出），二选一：
  - 走子：`{"type":"move","to":[r,c]}`
  - 放墙：`{"type":"wall","wall":{"wr":..,"wc":..,"orientation":"H"}}`（H 横墙 / V 竖墙，长 2）

非法决策判负（或由测试框架按犯规处理，请务必先用本地规则引擎自检合法性）。

## 3. 最小示例

见 `backend/app/ai_cpp_example/ai_example.cpp`（随机策略，仅演示协议）：

```bash
g++ -std=c++17 -O2 -o ai_example ai_example.cpp
./ai_example < kifu.json
```

Python 随机示例见 `backend/app/engine.py::random_ai_move`，可作为基线对手。

## 4. 规则要点（AI 需注意）

1. 死点不可进入；流沙进入后**对方连续行动两次**。
2. 墙为长 2 的直墙（横/竖）；A、B 为相互独立的获胜列集（可相交）。
3. 堵墙允许重叠检测（复用边即非法）；允许把路堵死，但被围者按规则 4 **直接获胜**，围墙者慎用。
4. 到达己方获胜点立即获胜；简化规则：不可进入对方所在格，无跳子。
