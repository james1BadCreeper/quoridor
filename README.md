## 技术栈

采用 React + Python(FastAPI)。

## 规则说明

该游戏为 Quoridor 的改版，规则变动如下：

1. 地图为 $n\times m$ 随机，$n,m\in [9,15]$。双方初始时各有 $v=(n+1)\times (m+1)\times \frac 1 10$ 个墙，向下取整，均为长 $2$ 的直墙（横/竖）。
2. 获胜点不再是对面的一行的全部点，而是由一个玩家选择两个大小为 $m/2$ 的集合 $A,B$，而另一个玩家选择先手、集合 $A$ 为获胜点或者后手、集合 $B$ 为获胜点。进行选择的玩家随机。
3. 地图上会随机较少量的死点（不能进入）和较少量的流沙（如果进入，对方连续行动两次）。
4. 如果成功将对方围死（让对方无论如何行动都无法获胜），那么被围死者直接获胜。

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

- 后端 `backend/app/engine.py`：随机地图、直墙（长 2）、A/B 获胜列集（相互独立）、死点/流沙、流沙罚步、无路径即被围判胜。
- 接口：开局 `/api/games/new`（支持人类指定 A/B 列集）、走子/放墙、合法走子查询、随机示例 AI（`/api/games/{id}/ai-move`）、棋谱导出/导入。
- 前端：深色现代 UI（SVG 棋盘：墙体着色绘制、悬停预览、点击间隙放墙）；开局向导完整还原规则 2（随机出题人 → 人类点选/AI 随机出 A/B → 对方选“先手 + A / 后手 + B”）；本地双人 / 人机混战 / AI 走到底演示、棋谱 json 导出导入、快照回放条。
- AI：Python 随机基线（`random_ai_move`）+ C++ 单文件示例与协议说明（`backend/app/ai_cpp_example/README.md`）。

## AI 编写指南

本项目的最大功能是测试编写的 AI 的性能。本节将指导你如何编写你自己的 AI。

打包好的 AI 应该是一个 .zip 文件，里面包含 cpp 代码，实现一个输入当前棋谱的 json，返回决策。