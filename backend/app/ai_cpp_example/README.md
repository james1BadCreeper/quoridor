# AI 编写指南

AI 是一个程序：从 stdin 读入一个 json，向 stdout 输出一行决策 json。
源码打成 `.zip` 上传，后端在 docker 沙箱里编译、运行、对战（无网络、256MB 内存、单步超时判负）。

## 本目录文件

| 文件 | 说明 |
|---|---|
| `ai_example.cpp` | 贪心示例（最短路＋挡墙＋技能），照抄结构即可 |
| `ai_random.cpp` | 随机示例（合法性优先，弱策略也必须输出合法） |
| `ai_common.hpp` | 公共库，见下 |
| `json.hpp` | nlohmann/json 单头文件，打包时**须一起打进 zip** |
| `Dockerfile[.random]`／`build.sh`／`run.sh` | 容器内编译＋沙箱运行脚本 |

## `ai_common.hpp` 用法

```cpp
#include "ai_common.hpp"
Board b = parseBoard(j);   // j 为输入 json，b.turn 即本 AI 执子
```

| 函数 | 作用 |
|---|---|
| `parseBoard(j)` | 解析棋谱 → `Board`（棋子/墙/死点/流沙/目标列/手牌/余墙/buff 全在里面） |
| `stepNeighbors(b, from, me, opp, blocked)` | 单步可达格（含跳子规则），`blocked = buildBlocked(b, {}, b.phased)` |
| `bfsDist(b, player, blocked)` | 到目标的最短步数表，到不了为 `INF` |
| `checkMove(b, to)` | 走子是否合法 → `{ok, reason}` |
| `checkWall(b, w, forbidSurround=true)` | 放墙是否合法 → `{ok, reason}`；`true` 时断路也算非法，执意围死传 `false`（引擎允许围死，被围者直接获胜） |
| `sandOk(b, c)` | 流沙陷阱落点是否合法（禁死点/已有流沙/棋子格/获胜点） |
| `wallJson(w)` | `WallSpec` 转输出格式 |
| `rng()` | 随机数引擎 |

注意：json 取值一律用 `getInt/getStr` 或显式转换（nlohmann 的算术转换是 explicit 的）。

## 阶段零：出题与选边（各 1 次）

实现内容：出题方定 A/B 列集，另一方选边（先手+A / 后手+B）。

- 出题输入：`{"phase":"goals","m":m}` → 输出：`{"goal_A":[...],"goal_B":[...]}`（各 m//2 列，范围内、无重复，A/B 可相交）
- 选边输入：`{"phase":"side","n":..,"m":..,"deads":..,"sands":..,"goal_A":..,"goal_B":..}` → 输出：`{"side":"first"}` 或 `{"side":"second"}`

非法出题/选边判负。出题时只传一方当出题人（`POST /api/ai/match` 的 `chooser`，或向导里 AI 出题席）。

## 阶段一：选技能卡（1 次）

实现内容：按 `skill_k` 和开局信息挑 k 张牌（可重复，对方不可见）。

- 输入：`{"phase":"select","skill_k":k,"n":..,"m":..,"deads":..,"sands":..,"goal_A":..,"goal_B":..}`
- 输出：`{"skills":["phase_walk","make_sand"]}`（恰好 k 张，id 见 `GET /api/skills`）

## 阶段二：每轮行动（多次）

实现内容：读棋谱，输出一个行动；行动前可先打一张手牌（不占轮次，整轮最多一张）。

输入（`GET /api/games/{id}/export` 的返回）：

```json
{
  "n": 9, "m": 9,
  "pawns": [[0, 4], [8, 4]], "turn": 0,
  "walls": [{"wr": 2, "wc": 3, "orientation": "H"}],
  "walls_left": [10, 10],
  "deads": [[4, 4]], "sands": [[5, 5]],
  "goal_A": [0, 1, 2, 3], "goal_B": [5, 6, 7, 8],
  "hands": [{"phase_walk": 1}, {}],
  "phase_buff": [false, false], "free_buff": [false, false],
  "l_bonus": [0, 0], "must_move": false, "seq_skill_used": false,
  "bonus_moves": 0
}
```

- 先手顶行出发、目标底行列 ∈ A；后手反之。`turn` 为行动方（即你）；`must_move=true` 时只能走子。
- 输出（**一行** json）：
  - 走子：`{"type":"move","to":[r,c]}`
  - 放墙：`{"type":"wall","wall":{"wr":..,"wc":..,"orientation":"H"}}`（直墙 H/V）；
    L 墙（需改造权）：`{"type":"wall","wall":{"wr":..,"wc":..,"kind":"L","arm":"NW"}}`
  - 打牌＋行动：`{"skill":"double_move","action":{"type":"move","to":[r,c]}}`；
    流沙陷阱须带落点：`{"skill":"make_sand","to":[r,c],"action":{...}}`

判负：输出非法 json、选牌/行动非法、进程崩溃、无输出、超时。

## 编译、测试、上传

```bash
./build.sh                            # 容器内编译两个示例
./run.sh < kifu.json                  # 沙箱跑贪心示例（`./run.sh random` 跑随机示例）
echo '{"phase":"select","skill_k":2}' | ./run.sh   # 选牌自测
zip my_ai.zip my_ai.cpp ai_common.hpp json.hpp
curl -F "file=@my_ai.zip" http://127.0.0.1:8000/api/ai/upload        # → aid
curl -X POST http://127.0.0.1:8000/api/ai/match \
  -H 'Content-Type: application/json' -d '{"white":"<aid>","black":"random","seed":7}'
```

上传规范（扩展名白名单、64 文件/8MB 上限、拍平单目录）与默认 AI 列表见根目录 `README.md《AI 编写指南》`。

## 规则要点

1. 死点不可进；踩流沙则**对方连走两次**，赶路应避开（首末行无死点/流沙）。
2. 墙长 2（横/竖），改造后可放 1+1 的 L 墙；放墙不重边；允许堵死，但被围者**直接获胜**。
3. 跳子：邻对方棋子时直线跳过，被挡则走其两侧斜格，对方格不可停。
4. 到达己方获胜点即胜；连续行动两步须走子；流沙陷阱禁死点/已有流沙/棋子格/获胜点；打牌双方得知。
