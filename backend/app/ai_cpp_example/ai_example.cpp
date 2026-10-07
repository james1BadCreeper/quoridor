// 示例 AI（C++，贪心策略）：从 stdin 读入 json，向 stdout 输出一行决策 json。
// 规则解析与合法性复算见 ai_common.hpp（与后端 engine 完全一致，可直接照抄）。
//
// 两阶段协议（与 GET /api/games/{id}/export 的棋谱格式一致）：
//   阶段一 选技能卡：输入 {"phase":"select","skill_k":k,...}，
//                   输出 {"skills":[...]}（恰好 k 个，可重复）。
//   阶段二 每轮行动：输入当前棋谱 json，输出：
//     不用技能：{"type":"move","to":[r,c]}
//               {"type":"wall","wall":{"wr":..,"wc":..,"orientation":"H"}}（直墙 H/V）
//               {"type":"wall","wall":{"wr":..,"wc":..,"kind":"L","arm":"NW"}}（L 墙，需改造权）
//     先用技能：{"skill":"double_move","action":{"type":"move","to":[r,c]}}
//               {"skill":"phase_walk","action":{"type":"move","to":[r,c]}}
//               {"skill":"free_wall","action":{"type":"wall","wall":{...}}}
//               {"skill":"l_remodel","action":{"type":"wall","wall":{L 墙 ...}}}
//               {"skill":"make_sand","to":[r,c],"action":{...}}（to 为流沙落点）
//
// 贪心策略：
//   - 走子：BFS 算己方到获胜点的最短路，沿最短路走一步，严重回避流沙。
//   - 放墙：枚举候选直墙，选“对方最短路增量最大、且己方仍有路”的；偶尔用 L 墙。
//   - 技能：距目标较远时打连续行动/穿墙；放墙前打免费墙；开局在对方必经之路埋流沙。
//   - 所有输出均经本地合法性复算，保证落子必合法（避免被判负）。
//
// 编译（docker，见本目录 Dockerfile / build.sh）。

#include <algorithm>
#include <iostream>

#include "ai_common.hpp"

// 在 legal 内选“最短路距离最小、非流沙优先”的走子
static Cell bestStep(const Board &b, const std::vector<std::vector<int>> &dist,
                     const std::vector<Cell> &legal) {
    Cell best = legal[0];
    int bestScore = INF;
    for (Cell c : legal) {
        int d = dist[c.first][c.second];
        int score = d + (b.sands.count(c) ? 1000 : 0);  // 重罚流沙
        if (score < bestScore) {
            bestScore = score;
            best = c;
        }
    }
    return best;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    std::string input((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
    if (input.empty()) {
        std::cout << "{\"type\":\"move\",\"to\":[1,4]}";
        return 0;
    }
    json j = json::parse(input, nullptr, false);
    if (j.is_discarded()) {  // 解析失败：给一个大概率合法的兜底
        std::cout << "{\"type\":\"move\",\"to\":[1,4]}";
        return 0;
    }

    // ===== 阶段一：选技能卡 =====
    if (getStr(j, "phase", "") == "select") {
        int k = getInt(j, "skill_k", 2);
        // 偏好顺序：连续行动 > 穿墙 > 免费墙 > 流沙陷阱 > 改造，循环填满
        const std::vector<std::string> pref = {"double_move", "phase_walk", "free_wall",
                                               "make_sand", "l_remodel"};
        json arr = json::array();
        for (int i = 0; i < k; ++i) arr.push_back(pref[i % (int)pref.size()]);
        std::cout << json({{"skills", arr}}).dump();
        return 0;
    }

    // ===== 阶段零之一：出题（选 A/B 获胜列集） =====
    // 选边在后、执子未知，故出对称题：A=B=中间 k 列，把选择留给选边阶段。
    if (getStr(j, "phase", "") == "goals") {
        int m = getInt(j, "m", 9);
        int k = m / 2;
        int start = (m - k) / 2;
        json cols = json::array();
        for (int c = start; c < start + k; ++c) cols.push_back(c);
        std::cout << json({{"goal_A", cols}, {"goal_B", cols}}).dump();
        return 0;
    }

    // ===== 阶段零之二：选边（先手+A / 后手+B） =====
    // 按双方起点到目标的最短距离（只考虑死点）挑近的一边，打平要先手。
    if (getStr(j, "phase", "") == "side") {
        Board b = parseBoard(j);
        auto d0 = bfsDist(b, 0, {});
        auto d1 = bfsDist(b, 1, {});
        int distFirst = d0[0][b.m / 2];
        int distSecond = d1[b.n - 1][b.m / 2];
        std::string side = distFirst <= distSecond ? "first" : "second";
        std::cout << json({{"side", side}}).dump();
        return 0;
    }

    // ===== 阶段二：行动（一次输出覆盖整轮；连续行动须一次输出两步） =====
    Board b = parseBoard(j);
    auto useSkill = [&](const std::string &s) { return b.hand.count(s) && b.hand[s] > 0; };

    auto blocked = buildBlocked(b, {}, b.phased);
    auto legal = stepNeighbors(b, b.me, b.me, b.opp, blocked);
    if (legal.empty()) {  // 无路（几乎不可能）：认输式输出
        std::cout << "{\"type\":\"move\",\"to\":[" << b.me.first << "," << b.me.second << "]}";
        return 0;
    }
    auto distMe = bfsDist(b, b.turn, blocked);
    auto blockedOpp = buildBlocked(b, {}, false);
    auto distOpp = bfsDist(b, 1 - b.turn, blockedOpp);
    int myD = distMe[b.me.first][b.me.second];
    int oppD = distOpp[b.opp.first][b.opp.second];

    // --- 技能 1：流沙陷阱（对方有明确短路时，在其下一步埋雷） ---
    if (useSkill("make_sand") && oppD < INF) {
        auto oppLegal = stepNeighbors(b, b.opp, b.opp, b.me, blockedOpp);
        Cell target = {-1, -1};
        for (Cell c : oppLegal) {
            if (distOpp[c.first][c.second] == oppD - 1 && sandOk(b, c)) {
                target = c;
                break;
            }
        }
        if (target.first >= 0) {
            Cell s = bestStep(b, distMe, legal);
            json to = json::array({target.first, target.second});
            json out = decision("make_sand", to, {moveAct(s.first, s.second)});
            std::cout << checked(b, out, legal).dump();
            return 0;
        }
    }

    // --- 放墙评估：找对方增量最大、己方仍有路的直墙（L 墙只能配改造技能现打现放，见下） ---
    WallSpec bestWall;
    bool hasWall = false;
    int bestGain = 0;
    bool canWall = (b.wallsLeft > 0 || b.freeWall);
    if (canWall && oppD < INF) {
        std::set<std::pair<Cell, Cell>> existing = buildBlocked(b, {}, false);
        std::vector<WallSpec> cands;
        for (int wr = 1; wr <= b.n - 1; ++wr)
            for (int wc = 0; wc <= b.m - 2; ++wc) cands.push_back({wr, wc, "straight", "H", "NW"});
        for (int wr = 0; wr <= b.n - 2; ++wr)
            for (int wc = 1; wc <= b.m - 1; ++wc) cands.push_back({wr, wc, "straight", "V", "NW"});
        std::shuffle(cands.begin(), cands.end(), rng());  // 同分随机，打破固定偏移
        int checked = 0;
        for (auto &w : cands) {
            if (++checked > 600) break;  // 限时：大棋盘只抽查前 600 候选
            if (!wallInBounds(b, w)) continue;
            std::set<std::pair<Cell, Cell>> e;
            wallEdges(e, w.wr, w.wc, w.kind, w.ori, w.arm);
            bool overlap = false;
            for (auto &x : e)
                if (existing.count(x)) {
                    overlap = true;
                    break;
                }
            if (overlap) continue;
            auto nb = buildBlocked(b, {w}, false);
            auto dMe = bfsDist(b, b.turn, nb);
            if (dMe[b.me.first][b.me.second] >= INF) continue;  // 己方不能断路（避免送围死胜）
            auto dOpp = bfsDist(b, 1 - b.turn, nb);
            int gain = dOpp[b.opp.first][b.opp.second] - oppD;
            if (gain > bestGain) {
                bestGain = gain;
                bestWall = w;
                hasWall = true;
            }
        }
    }

    // --- 技能 2：连续行动（距目标远时，一次输出两步） ---
    if (useSkill("double_move") && myD > 3 && myD < INF) {
        Cell s1 = bestStep(b, distMe, legal);
        Board b2 = b;
        b2.me = s1;
        auto legal2 = stepNeighbors(b2, b2.me, b2.me, b2.opp, buildBlocked(b2, {}, b2.phased));
        Cell s2 = legal2.empty() ? s1 : bestStep(b2, distMe, legal2);
        json out = decision("double_move", json(nullptr),
                            {moveAct(s1.first, s1.second), moveAct(s2.first, s2.second)});
        std::cout << checked(b, out, legal).dump();
        return 0;
    }
    // --- 技能 3：穿墙（被墙严重绕路时，用无视墙视角走一步） ---
    if (useSkill("phase_walk") && myD > 6 && myD < INF) {
        auto distPhase = bfsDist(b, b.turn, {});
        auto legalPhase = stepNeighbors(b, b.me, b.me, b.opp, {});
        if (!legalPhase.empty()) {
            Cell s = bestStep(b, distPhase, legalPhase);
            json out = decision("phase_walk", json(nullptr), {moveAct(s.first, s.second)});
            std::cout << checked(b, out, legalPhase).dump();
            return 0;
        }
    }
    // --- 放墙（含免费墙） ---
    if (hasWall && bestGain >= 2 && (b.freeWall || (rng()() % 100 < 30))) {
        json wj = wallJson(bestWall);
        if (b.freeWall && useSkill("free_wall")) {
            json out = decision("free_wall", json(nullptr), {wallAct(wj)});
            std::cout << checked(b, out, legal).dump();
            return 0;
        }
        if (!b.freeWall) {
            json out = decision("", json(nullptr), {wallAct(wj)});
            std::cout << checked(b, out, legal).dump();
            return 0;
        }
    }
    // L 墙机会：无直墙好位但有改造卡时，现打现放
    if (!hasWall && useSkill("l_remodel") && canWall) {
        std::set<std::pair<Cell, Cell>> existing = buildBlocked(b, {}, false);
        const std::string arms[4] = {"NW", "NE", "SW", "SE"};
        for (int t = 0; t < 200; ++t) {
            WallSpec w{(int)(rng()() % (b.n - 1)) + 1, (int)(rng()() % (b.m - 1)) + 1,
                        "L", "", arms[rng()() % 4]};
            std::set<std::pair<Cell, Cell>> e;
            wallEdges(e, w.wr, w.wc, "L", "", w.arm);
            bool overlap = false;
            for (auto &x : e)
                if (existing.count(x)) {
                    overlap = true;
                    break;
                }
            if (overlap) continue;
            auto nb = buildBlocked(b, {w}, false);
            if (bfsDist(b, b.turn, nb)[b.me.first][b.me.second] >= INF) continue;
            json out = decision("l_remodel", json(nullptr), {wallAct(wallJson(w))});
            std::cout << checked(b, out, legal).dump();
            return 0;
        }
    }

    // --- 默认：沿最短路走一步 ---
    Cell s = bestStep(b, distMe, legal);
    json out = decision("", json(nullptr), {moveAct(s.first, s.second)});
    std::cout << checked(b, out, legal).dump();
    return 0;
}
