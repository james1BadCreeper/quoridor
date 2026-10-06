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
  "skill_k": 2, "hands": [{"phase_walk": 1}, {}],
  "started": true, "seq_skill_used": false,
  "history": [{"player": 0, "type": "move", "to": [1, 4]}]
}
```

- 先手出生顶行、目标为底行中列 ∈ A；后手出生底行、目标为顶行中列 ∈ B。
- `turn` 为轮到行动方；`bonus_moves>0` 表示因流沙同一人继续行动。

## 2. AI 输入输出协议

打包好的 AI 是一个 `.zip` 文件，里面包含 cpp 代码，分两个阶段与框架交互：

**阶段一：赛前选技能卡。** 输入 `{"phase":"select","skill_k":k,"n":..,"m":..,"deads":..,"sands":..,"goal_A":..,"goal_B":..}`，
输出 `{"skills":["phase_walk","make_sand"]}`（恰好 k 张，可重复）。

**阶段二：每轮行动。** 输入当前棋谱 json，输出一行 json：
- 不用技能：`{"type":"move","to":[r,c]}` 或 `{"type":"wall","wall":{"wr":..,"wc":..,"orientation":"H"}}`。
- 先用技能：`{"skill":"double_move","to":[r,c],"action":{"type":"move","to":[r,c]}}`
 （`skill` 为技能 id，`to` 仅流沙陷阱使用；连续行动序列中整轮最多带一张技能）。

不使用技能的旧 AI 依然有效（`skill` 缺省即可）。

## 3. 最小示例

见 `backend/app/ai_cpp_example/ai_example.cpp`（随机策略，仅演示协议）：

```bash
g++ -std=c++17 -O2 -o ai_example ai_example.cpp
./ai_example < kifu.json
```

Python 随机示例见 `backend/app/engine.py::random_ai_move`，可作为基线对手。

## 4. 规则要点（AI 需注意）

1. 死点不可进入；流沙进入后**对方连续行动两次**（首末行天然无死点/流沙）。
2. 墙为长 2 的直墙（横/竖）；打出“改造”技能后可放置 1+1 的 L 墙；A、B 为相互独立的获胜列集（可相交）。
3. 堵墙允许重叠检测（复用边即非法）；允许把路堵死，但被围者按规则 4 **直接获胜**，围墙者慎用。
4. 到达己方获胜点立即获胜；跳子规则：相邻对方棋子时直线跳过，直线落点被挡（出界/死点/墙）则改走其两侧斜格，对方所在格不可停留。
5. 技能：行动前可打出一张（不占轮次），连续行动序列中最多一张；“连续行动”的两步都必须是走子，踩中流沙则剩余步数被覆盖；
   “流沙陷阱”禁死点/已有流沙/棋子格/获胜点；使用技能双方均会得知。
