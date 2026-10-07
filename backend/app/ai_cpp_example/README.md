# AI 编写指南

本项目的最大功能是测试编写的 AI 的性能。本节指导你如何编写自己的 AI。

## 0. 本目录文件

- `ai_example.cpp`：贪心示例 AI（BFS 最短路推进、挡路墙、四技能启发式），可直接照抄结构。
- `json.hpp`：捆绑的 nlohmann/json 单头文件（v3.11.3），打包时**须一起打进 zip**（或换你自己的解析方式）。
- `Dockerfile`：容器内编译模板（COPY 源码 → g++ 编译 → ENTRYPOINT 为二进制），自己的 AI 照此模式写。
- `build.sh`：`docker build -t quoridor-ai-example .` + 选牌自检。
- `run.sh`：沙箱运行（`--network none`，256MB 内存），stdin 读 json，stdout 输出一行决策。

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
- 不用技能：`{"type":"move","to":[r,c]}` 或 `{"type":"wall","wall":{"wr":..,"wc":..,"orientation":"H"}}`
 （直墙 H/V；L 墙需改造权：`{"wr":..,"wc":..,"kind":"L","arm":"NW"}`）。
- 先用技能：`{"skill":"double_move","to":[r,c],"action":{"type":"move","to":[r,c]}}`
 （`skill` 为技能 id，`to` 仅流沙陷阱使用；连续行动序列中整轮最多带一张技能）。

不使用技能的旧 AI 依然有效（`skill` 缺省即可）。

## 3. 编译与本地测试（docker 内编译）

```bash
./build.sh              # 容器内 g++ -std=c++17 -O2 编译，得到 quoridor-ai-example
./run.sh < kifu.json    # 沙箱运行，stdout 输出一行决策 json
echo '{"phase":"select","skill_k":2}' | ./run.sh   # 选牌阶段自测
```

`kifu.json` 取自 `GET /api/games/{id}/export`。注意：json 解析一律显式转换类型
（nlohmann::json 的算术转换是 explicit 的，隐式塞进 pair/容器会踩坑，见示例 `getInt/getStr`）。

Python 随机示例见 `backend/app/engine.py::random_ai_move`，可作为基线对手。

## 4. 上传与对战

```bash
zip my_ai.zip ai_example.cpp json.hpp   # 你的源码（多文件一起编译链接）
curl -F "file=@my_ai.zip" http://127.0.0.1:8000/api/ai/upload   # 容器内编译 → aid
curl -X POST http://127.0.0.1:8000/api/ai/match \
  -H 'Content-Type: application/json' -d '{"white":"<aid>","black":"random","seed":7}'
```

上传规范（扩展名白名单、64 文件/8MB 上限、超时判负等）见根目录 `README.md《AI 编写指南》`。

## 5. 规则要点（AI 需注意）

1. 死点不可进入；流沙进入后**对方连续行动两次**（首末行天然无死点/流沙）。
2. 墙为长 2 的直墙（横/竖）；打出“改造”技能后可放置 1+1 的 L 墙；A、B 为相互独立的获胜列集（可相交）。
3. 堵墙允许重叠检测（复用边即非法）；允许把路堵死，但被围者按规则 4 **直接获胜**，围墙者慎用。
4. 到达己方获胜点立即获胜；跳子规则：相邻对方棋子时直线跳过，直线落点被挡（出界/死点/墙）则改走其两侧斜格，对方所在格不可停留。
5. 技能：行动前可打出一张（不占轮次），连续行动序列中最多一张；“连续行动”的两步都必须是走子，踩中流沙则剩余步数被覆盖；
   “流沙陷阱”禁死点/已有流沙/棋子格/获胜点；使用技能双方均会得知。
