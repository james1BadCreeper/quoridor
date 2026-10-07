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
| `Dockerfile[.random]`／`build.sh`／`run.sh` | 内部构建脚本（用户测试走前端上传，无需使用） |

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
| `checkDecision(b, out)` | 整轮输出是否合法 → `{ok, reason}`（含流沙作废语义；输出前必调） |
| `moveAct(r,c)`／`wallAct(w)`／`decision(skill,to,acts)` | 组装行动与整轮输出，避免手写括号出错 |
| `sandOk(b, c)` | 流沙陷阱落点是否合法（禁死点/已有流沙/棋子格/获胜点） |
| `wallJson(w)` | `WallSpec` 转输出格式 |
| `rng()` | 随机数引擎 |

注意：json 取值一律用 `getInt/getStr` 或显式转换（nlohmann 的算术转换是 explicit 的）。

## JSON 解析（`json.hpp`，nlohmann/json）

```cpp
#include "ai_common.hpp"  // 已含 json.hpp，别名 json = nlohmann::json

// 读：整段 stdin 一次解析，失败直接给兜底（崩溃/无输出判负）
json j = json::parse(input, nullptr, false);
if (j.is_discarded()) { /* 输出兜底决策 */ }

// 读字段：先判类型再转；缺字段给缺省
int m = getInt(j, "m", 9);                    // 数字，无则 9
std::string arm = getStr(w, "arm", "NW");     // 字符串；后端直墙的 arm 为 null，按缺省走
for (auto &d : j.value("deads", json::array()))
    if (d.is_array() && d.size() == 2) deads.insert({(int)d[0], (int)d[1]});

// 写：直接构造，最后一行 dump 输出
json out = {{"type", "move"}, {"to", {r, c}}};
std::cout << out.dump();
```

三条铁律：

1. **一律显式转换**——`(int)x`、`(std::string)x`、`x.is_number()` 先判后取；隐式塞进 `pair`/容器会触发整对象转换导致崩溃（真实踩坑）。
2. **可空字段先判**——如墙的 `arm` 为 null 时必须走缺省，不能直接 `w.value("arm", "NW")`。
3. **输出必须一行合法 json**——多余 `cout` 调试信息会导致解析失败判负；数组按下标取前先判长度。

## 输入输出格式

### 阶段零之一：出题（`goals`，出题方 1 次）

输入：

| 字段 | 类型 | 说明 |
|---|---|---|
| `phase` | `"goals"` | 固定值 |
| `n` | int | 行数，9~15 |
| `m` | int | 列数，9~15 |
| `deads` | [r,c][] | 死点（预览地形，与开局一致） |
| `sands` | [r,c][] | 流沙（预览地形，与开局一致） |

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
| `skills` | string[] | 恰 k 张，可重复；id 为 `l_remodel`（改造现打现放：打出后本次行动必须放 L 墙，否则作废）／`double_move`（连续行动）／`phase_walk`（穿墙）／`make_sand`（流沙陷阱）／`free_wall`（免费墙） |

### 阶段二：每轮行动（精简快照输入，输出一行）

输入为当前棋盘状态（无历史操作、无可推导字段；完整棋谱格式见 `GET /api/games/{id}/export`，仅回放用）：

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
| `hand` | object | 自家手牌 `{技能id: 张数}`（打哪张就写哪张） |
| `opp_hand_count` | int | 对方剩余手牌总数（只给张数，不给明细） |
| `sand_bonus` | bool | 真＝因对方踩中流沙，本轮可行动两次**且可放墙**（与连续行动不同） |

wall 对象：

| 字段 | 类型 | 说明 |
|---|---|---|
| `wr`、`wc` | int | 墙锚点：直墙 H 要求 `1<=wr<=n-1, 0<=wc<=m-2`；V 要求 `0<=wr<=n-2, 1<=wc<=m-1`；L 要求 `1<=wr<=n-1, 1<=wc<=m-1` |
| `orientation` | `"H"`／`"V"` | 直墙方向（横阻纵向、竖阻横向）；L 墙无此字段 |
| `kind` | `"straight"`／`"L"` | 缺省为直墙；L 墙只能配改造技能同一次打出（裸 L 非法） |
| `arm` | `"NW"`／`"NE"`／`"SW"`／`"SE"` | 仅 L 墙：直角朝向 |

输出（**一行** json，一次覆盖整轮）：

```json
{"skill": "double_move", "actions": [{"type": "move", "to": [1, 4]}, {"type": "move", "to": [2, 4]}]}
```

| 部分 | 格式 | 说明 |
|---|---|---|
| `skill` | 可缺省 | 技能 id（本序列未用过、手牌须有；`make_sand` 须带落点 `"to":[r,c]`） |
| `actions` | 1~2 个对象 | `{"type":"move","to":[r,c]}` 走子；`{"type":"wall","wall":{...}}` 放墙（直墙 H/V；L 墙须配改造同打） |

执行与判负（后端逐条落子）：按顺序执行，轮到对方或终局后剩余作废——首步踩流沙则第二步无论是什么都作废，普通轮次多给的一步也作废；
形状错误（非对象、`actions` 不是 1~2 个）直接判负；技能非法、首步非法、该走时第二步非法直接判负。
连续行动须一次输出两步（只给一步直接判负），两步都必须是走子。输出前务必调 `checkDecision(b, out)` 自检（语义与后端一致；`checked` 会在不过时退化保底）。

判负：输出非法 json、选牌/出题/选边/行动非法、进程崩溃、无输出、超时。

## 上传与对战

在前端开局向导的 AI 席位旁点「上传 AI」，选择 zip（只需你自己的源码）即可测试；
编译通过后自动选中，直接开局或 AI 走到底。接口方式：

```bash
zip my_ai.zip my_ai.cpp               # 只需打包你自己的源码（ai_common.hpp/json.hpp 后端自动提供）
curl -F "file=@my_ai.zip" http://127.0.0.1:8000/api/ai/upload        # → aid
curl -X POST http://127.0.0.1:8000/api/ai/match \
  -H 'Content-Type: application/json' -d '{"white":"<aid>","black":"random","seed":7}'
```

上传规范（扩展名白名单、64 文件/8MB 上限、拍平单目录）与默认 AI 列表见根目录 `README.md《AI 编写指南》`。

## 规则要点

1. 死点不可进；踩流沙则**对方连走两次**，赶路应避开（首末行无死点/流沙）。
2. 墙长 2（横/竖），改造现打现放 1+1 的 L 墙（不放即作废）；放墙不重边；允许堵死，但被围者**直接获胜**。
3. 跳子：邻对方棋子时直线跳过，被挡则走其两侧斜格，对方格不可停。
4. 到达己方获胜点即胜；连续行动两步须走子且一次输出（对方踩流沙送的两次**可放墙**）；流沙陷阱禁死点/已有流沙/棋子格/获胜点；打牌双方得知。
