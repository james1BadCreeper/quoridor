## 技术栈

采用 React + Python(FastAPI)。

## 规则说明

该游戏为 Quoridor 的改版，规则变动如下：

1. 地图为 $n\times m$ 随机，$n,m\in [9,15]$。双方初始时各有 $v=(n+1)\times (m+1)\times \frac 1 10$ 个墙，向下取整，均为长 $2$ 的直墙（横/竖）。
2. 获胜点不再是对面的一行的全部点，而是由一个玩家选择两个大小为 $m/2$ 的集合 $A,B$，而另一个玩家选择先手、集合 $A$ 为获胜点或者后手、集合 $B$ 为获胜点。进行选择的玩家随机。
3. 地图上会随机较少量的死点（不能进入）和较少量的流沙（如果进入，对方连续行动两次）。
4. 如果成功将对方围死（让对方无论如何行动都无法获胜），那么被围死者直接获胜。
5. 技能卡：选定获胜点、知晓地图后，双方各选 $k$ 张技能卡（$k=F(n,m)=\max(2, v//5)$，最小地图取 $2$，可重复，对方不可见，同机对战除外）。技能为：改造（获得 1 次 L 形墙放置权）、连续行动（本回合连走两次且都必须是移动）、穿墙（下次走子无视墙）、流沙陷阱（禁获胜点）、免费墙（下次放墙不耗存量）。行动前可打出一张，不占轮次；连续行动序列中最多打出一张；使用时双方均得知。

## 游玩支持

支持人类玩家和 AI 玩家，可以都是人或者都是 AI。

支持导出 json 格式棋谱并查看回放。

## 运行方式

后端：

```bash
pip install -r backend/requirements.txt
uvicorn app.main:app --reload --app-dir backend
```

前端：

```bash
npm install   # 国内网络慢可用镜像源
npm run dev   # 访问 http://127.0.0.1:5173（/api 已代理到 8000 端口）
```

测试：`python3 -m pytest backend/tests -q`；C++ 示例见 `backend/app/ai_cpp_example/`。

## 实现现状（MVP）

- 后端 `backend/app/engine.py`：随机地图、直墙（长 2，改造技能可放 L 墙）、A/B 获胜列集（相互独立）、死点/流沙（首末行无死点流沙）、流沙罚步、无路径即被围判胜、技能系统（手牌/序列锁/免费墙等 5 种）。
- 接口：开局 `/api/games/new`（支持人类指定 A/B 列集，返回 `skill_k`）、赛前选技能（`/skills/select`、`/skills/random`）、行动前打出手牌（`/skills/play`）、走子/放墙、合法走子查询、随机示例 AI（偶尔用技能）、棋谱导出/导入。
- 前端：深色现代 UI；开局向导（随机出题人 → 人类点选/AI 随机出 A/B → 对方选边 → 双方选技能卡）；技能面板（同机手牌互可见）、L 墙放置与流沙选格；本地双人 / 人机混战 / AI 走到底演示、棋谱 json 导出导入、快照回放条。
- AI：Python 随机基线（`random_ai_move`）+ C++ 贪心示例（BFS 最短路推进、挡路墙、四技能启发式，捆绑 nlohmann/json，docker 内编译）；外部 AI 上传/编译/对战接口（`POST /api/ai/upload`、`POST /api/ai/match`，沙箱 `--network none` + 超时 + 256MB 内存）。

## AI 编写指南

本项目的最大功能是测试编写的 AI 的性能。本节指导你如何编写自己的 AI。

### 1. 总览

- AI 是一个程序：从 stdin 读入一个 json（当前局面），向 stdout 输出一行决策 json。
- 语言不限（示例用 C++，可直接照抄）；源码打成 `.zip` 上传，后端在 docker 沙箱里编译、运行、对战。
- 两阶段协议：赛前选技能卡（1 次）→ 每轮行动（多次）。
- 验证链路（由弱到强）：本地 `g++` 直编直跑 → `build.sh` + `run.sh` 走 docker 自测 → 上传后端与示例 AI / random 基线对战。

### 2. 输入输出协议

棋谱格式与 `GET /api/games/{id}/export` 的返回一致，详见 `backend/app/ai_cpp_example/README.md`。

**阶段一：赛前选技能卡。** 输入 `{"phase":"select","skill_k":k,"n":..,"m":..,"deads":..,"sands":..,"goal_A":..,"goal_B":..}`，
输出 `{"skills":[...]}`（恰好 k 张，可重复；技能 id 见 `GET /api/skills`）。

**阶段二：每轮行动。** 输入当前棋谱 json，输出一行 json：

- 不用技能：`{"type":"move","to":[r,c]}` 或 `{"type":"wall","wall":{"wr":..,"wc":..,"orientation":"H"}}`（直墙 H/V），
  L 墙（需改造权）：`{"type":"wall","wall":{"wr":..,"wc":..,"kind":"L","arm":"NW"}}`。
- 先用技能：`{"skill":"double_move","action":{"type":"move","to":[r,c]}}`；
  流沙陷阱须带落点：`{"skill":"make_sand","to":[r,c],"action":{...}}`。

不使用技能的旧 AI 依然有效（`skill` 缺省即可）。选牌/行动非法、输出非法、超时一律判负。

### 3. 打包规范（上传 zip）

- 只收 `.cpp/.cc/.c/.h/.hpp`；最多 64 个文件、解压后 ≤8MB；须至少包含一个源文件（多文件一起编译链接）。
- 编译出的二进制即 AI 本体：固定从 stdin/stdout 按协议交互，无参数、无网络。
- 单步默认超时 5 秒（可调，上限 30 秒），内存 256MB；超时/崩溃/无输出判负。

### 4. 本地 docker 编译运行（以示例 AI 为例）

```bash
cd backend/app/ai_cpp_example
./build.sh            # 容器内 g++ 编译，得到镜像 quoridor-ai-example
./run.sh < kifu.json  # 沙箱运行（--network none），stdout 输出一行决策
```

其中 `kifu.json` 可由对局导出：`GET /api/games/{id}/export`；选牌阶段自测：

```bash
echo '{"phase":"select","skill_k":2}' | ./run.sh
```

### 5. 上传与对战（后端接口）

```bash
# 上传源码 zip 并在容器内编译 → 返回 aid
curl -F "file=@my_ai.zip" http://127.0.0.1:8000/api/ai/upload
# 查看已上传 AI 与编译状态
curl http://127.0.0.1:8000/api/ai/list
# 已上传 AI 在指定对局走一步（人机对战、演示用）
curl -X POST http://127.0.0.1:8000/api/games/{gid}/ai-external-move \
  -H 'Content-Type: application/json' -d '{"aid":"<aid>"}'
# 自动对战（双方各为 "random" 或 aid，直至终局/犯规/超步数）
curl -X POST http://127.0.0.1:8000/api/ai/match \
  -H 'Content-Type: application/json' -d '{"white":"<aid>","black":"random","seed":7}'
```

犯规（非法决策）、超时者判负；超出步数上限判平局（`winner=-1`）。

### 6. 规则要点（AI 需注意）

1. 死点不可进入；流沙进入后**对方连续行动两次**（首末行天然无死点/流沙），正常赶路应避开流沙。
2. 墙为长 2 的直墙（横/竖）；打出“改造”技能后可放置 1+1 的 L 墙；A、B 为相互独立的获胜列集（可相交）。
3. 堵墙允许重叠检测（复用边即非法）；允许把路堵死，但被围者按规则 4 **直接获胜**，围墙者慎用。
4. 到达己方获胜点立即获胜；跳子规则：相邻对方棋子时直线跳过，直线落点被挡（出界/死点/墙）则改走其两侧斜格，对方所在格不可停留。
5. 技能：行动前可打出一张（不占轮次），连续行动序列中最多一张；“连续行动”的两步都必须是走子，踩中流沙则剩余步数被覆盖；
   “流沙陷阱”禁死点/已有流沙/棋子格/获胜点；使用技能双方均会得知。

### 7. 基线强度

- Python random 基线见 `backend/app/engine.py::random_ai_move`，可作为最弱对手。
- C++ 贪心示例（`ai_example.cpp`）：BFS 最短路推进、给对方挡路的墙、四技能启发式使用；
  实测 9×9 对 random 基线六战全胜（先/后手各三局，约 20 步终结）。