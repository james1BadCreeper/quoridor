# AI 编写指南

AI 是一个程序：从 stdin 读入一个 json，向 stdout 输出一行决策 json。
源码打成 `.zip` 上传，后端在 docker 沙箱里编译、运行、对战（无网络、256MB 内存、单步超时判负）。

## 本目录文件

| 文件 | 说明 |
|---|---|
| `ai_example.cpp` | 贪心示例（最短路＋挡墙＋技能），照抄结构即可 |
| `ai_random.cpp` | 随机示例（合法性优先，弱策略也必须输出合法） |
| `ai_common.hpp` | 公共库，见下 |
| `json.hpp` | nlohmann/json 单头文件，上传时**无需打包**（后端自动提供；自带同名文件则以你的为准） |
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

## 输入输出格式

### 阶段零之一：出题（`goals`，出题方 1 次）

输入：

| 字段 | 类型 | 说明 |
|---|---|---|
| `phase` | `"goals"` | 固定值 |
| `m` | int | 列数，9~15 |

输出：

| 字段 | 类型 | 说明 |
|---|---|---|
| `goal_A` | int[] | 先手获胜列，恰 m//2 个，`[0,m)` 内无重复 |
| `goal_B` | int[] | 后手获胜列，约束同上；A、B 之间可重复 |

### 阶段零之二：选边（`side`，另一方 1 次）

输入：

| 字段 | 类型 | 说明 |
|---|---|---|
| `phase` | `"side"` | 固定值 |
| `n`、`m` | int | 行数、列数 |
| `deads` | `[r,c][]` | 死点格（不可进入） |
| `sands` | `[r,c][]` | 流沙格（踩中后对方连走两次） |
| `goal_A`、`goal_B` | int[] | 出题方定的获胜列 |

输出：

| 字段 | 类型 | 说明 |
|---|---|---|
| `side` | `"first"`／`"second"` | `first`=执先手＋A，`second`=执后手＋B |

### 阶段一：选技能卡（`select`，每方 1 次）

输入：

| 字段 | 类型 | 说明 |
|---|---|---|
| `phase` | `"select"` | 固定值 |
| `skill_k` | int | 须选张数（对方不可见，同机除外） |
| `n`、`m`、`deads`、`sands`、`goal_A`、`goal_B` | 同上 | 开局信息，供挑牌参考 |

输出：

| 字段 | 类型 | 说明 |
|---|---|---|
| `skills` | string[] | 恰 k 张，可重复；id 为 `l_remodel`（改造）／`double_move`（连续行动）／`phase_walk`（穿墙）／`make_sand`（流沙陷阱）／`free_wall`（免费墙） |

### 阶段二：每轮行动（棋谱输入，输出一行）

输入即 `GET /api/games/{id}/export` 的棋谱：

| 字段 | 类型 | 说明 |
|---|---|---|
| `n`、`m` | int | 行数、列数 |
| `pawns` | `[[r,c],[r,c]]` | [先手棋子，后手棋子]；`turn` 指向的那个就是你 |
| `turn` | 0／1 | 轮到行动方（即本次输入的执子） |
| `walls` | object[] | 已有墙，字段见下表 wall 对象 |
| `walls_left` | `[int,int]` | 双方剩余墙数 |
| `deads` | `[r,c][]` | 死点格 |
| `sands` | `[r,c][]` | 流沙格 |
| `goal_A`、`goal_B` | int[] | 获胜列；先手目标底行 ∈ A，后手目标顶行 ∈ B |
| `hands` | `[object,object]` | 双方手牌 `{技能id: 张数}`（传给 AI 的是其视角：对方手牌恒为空，只能看到自己的） |
| `phase_buff` | `[bool,bool]` | 穿墙 buff（下次走子无视墙，仍不能进死点） |
| `free_buff` | `[bool,bool]` | 免费墙 buff（下次放墙不耗存量） |
| `l_bonus` | `[int,int]` | L 墙放置权（打出改造获得） |
| `must_move` | bool | 真＝连续行动中，只能走子不能放墙 |
| `seq_skill_used` | bool | 真＝本序列已打出过技能，不能再打 |
| `bonus_moves` | int | ＞0＝同一人继续行动（流沙罚步或连续行动） |
| `skill_k` | int | 本局每人选牌数（复盘用） |
| `started` | bool | 双方选完牌后为真 |
| `winner`／`win_reason` | int／null＋string | 行动输入中恒为 null（终局不会再调 AI） |
| `history` | object[] | 历史记录（只读，供复盘）；条目见下表 |

wall 对象：

| 字段 | 类型 | 说明 |
|---|---|---|
| `wr`、`wc` | int | 墙锚点：直墙 H 要求 `1<=wr<=n-1, 0<=wc<=m-2`；V 要求 `0<=wr<=n-2, 1<=wc<=m-1`；L 要求 `1<=wr<=n-1, 1<=wc<=m-1` |
| `orientation` | `"H"`／`"V"` | 直墙方向（横阻纵向、竖阻横向）；L 墙无此字段 |
| `kind` | `"straight"`／`"L"` | 缺省为直墙；L 需改造权 |
| `arm` | `"NW"`／`"NE"`／`"SW"`／`"SE"` | 仅 L 墙：直角朝向 |

history 条目（`type` 区分）：

| `type` | 附加字段 | 说明 |
|---|---|---|
| `move` | `to:[r,c]` | 走子（含跳子落点） |
| `wall` | `wall:{...}` | 放墙（同 wall 对象） |
| `skill` | `skill:id`，流沙陷阱另带 `to:[r,c]` | 打出手牌（双方得知） |
| `select_skills` | `skills:[...]` | 赛前选牌（传给 AI 的视角中对方内容为空；全量导出/回放可见） |
| `quicksand` | — | 踩中流沙，对方连续行动两次 |

输出（**一行** json）：

| 格式 | 说明 |
|---|---|
| `{"type":"move","to":[r,c]}` | 走子（须在合法走子集内，含跳子） |
| `{"type":"wall","wall":{...}}` | 放墙（直墙 H/V；L 墙需改造权，见 wall 对象） |
| `{"skill":"<id>","action":{...}}` | 先打一张手牌再行动；`action` 为上两种之一 |
| `{"skill":"make_sand","to":[r,c],"action":{...}}` | 流沙陷阱须带落点 `to`（禁死点/已有流沙/棋子格/获胜点） |

判负：输出非法 json、选牌/出题/选边/行动非法、进程崩溃、无输出、超时。

## 编译、测试、上传

```bash
./build.sh                            # 容器内编译两个示例
./run.sh < kifu.json                  # 沙箱跑贪心示例（`./run.sh random` 跑随机示例）
echo '{"phase":"select","skill_k":2}' | ./run.sh   # 选牌自测
zip my_ai.zip my_ai.cpp               # 只需打包你自己的源码（ai_common.hpp/json.hpp 后端自动提供）
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
