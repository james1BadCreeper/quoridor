// 随机 AI（C++）：与贪心示例走同一套正规编写手段（ai_common.hpp 解析 + 两阶段协议）。
// 策略：选牌全随机；行动 25% 随机放墙、75% 随机走子，偶尔随机打出手牌。
// 所有输出均经本地合法性复算，保证落子必合法（避免被判负）。
//
// 编译（docker，见本目录 Dockerfile.random / build.sh）。

#include <iostream>

#include "ai_common.hpp"

// 枚举全部“不重边、双方不断路”的直墙/ L 墙候选（L 需 lBonus>0）
static std::vector<WallSpec> legalWallCands(const Board &b, bool allowL) {
    std::vector<WallSpec> out;
    std::set<std::pair<Cell, Cell>> existing = buildBlocked(b, {}, false);
    std::vector<WallSpec> cands;
    for (int wr = 1; wr <= b.n - 1; ++wr)
        for (int wc = 0; wc <= b.m - 2; ++wc) cands.push_back({wr, wc, "straight", "H", "NW"});
    for (int wr = 0; wr <= b.n - 2; ++wr)
        for (int wc = 1; wc <= b.m - 1; ++wc) cands.push_back({wr, wc, "straight", "V", "NW"});
    if (allowL) {
        const std::string arms[4] = {"NW", "NE", "SW", "SE"};
        for (int wr = 1; wr <= b.n - 1; ++wr)
            for (int wc = 1; wc <= b.m - 1; ++wc)
                for (auto &a : arms) cands.push_back({wr, wc, "L", "", a});
    }
    for (auto &w : cands) {
        if (w.kind == "L" && !allowL) continue;
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
        if (bfsDist(b, b.turn, nb)[b.me.first][b.me.second] >= INF) continue;
        if (bfsDist(b, 1 - b.turn, nb)[b.opp.first][b.opp.second] >= INF) continue;
        out.push_back(w);
    }
    return out;
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
    if (j.is_discarded()) {
        std::cout << "{\"type\":\"move\",\"to\":[1,4]}";
        return 0;
    }

    // ===== 阶段一：选技能卡（全随机，可重复） =====
    if (getStr(j, "phase", "") == "select") {
        int k = getInt(j, "skill_k", 2);
        const std::vector<std::string> ids = {"l_remodel", "double_move", "phase_walk",
                                              "make_sand", "free_wall"};
        json arr = json::array();
        for (int i = 0; i < k; ++i) arr.push_back(ids[rng()() % ids.size()]);
        std::cout << json({{"skills", arr}}).dump();
        return 0;
    }

    // ===== 阶段二：行动 =====
    Board b = parseBoard(j);
    auto pickMove = [&](bool phased) {
        auto legal = stepNeighbors(b, b.me, b.me, b.opp, buildBlocked(b, {}, phased));
        if (legal.empty()) return Cell{-1, -1};
        return legal[rng()() % legal.size()];
    };

    // 连续行动序列中只能走子
    if (b.mustMove) {
        Cell s = pickMove(b.phased);
        if (s.first < 0) s = b.me;
        std::cout << json({{"type", "move"}, {"to", {s.first, s.second}}}).dump();
        return 0;
    }

    // 20% 随机打出一张手牌（本序列未用过时）
    if (!b.seqSkillUsed && !b.hand.empty() && rng()() % 100 < 20) {
        std::vector<std::string> owned;
        for (auto &[id, cnt] : b.hand)
            if (cnt > 0) owned.push_back(id);
        if (!owned.empty()) {
            std::string sk = owned[rng()() % owned.size()];
            if (sk == "double_move" || sk == "phase_walk") {
                bool ph = (sk == "phase_walk") || b.phased;
                Cell s = pickMove(ph);
                if (s.first >= 0) {
                    json out = {{"skill", sk}, {"action", {{"type", "move"}, {"to", {s.first, s.second}}}}};
                    std::cout << out.dump();
                    return 0;
                }
            } else if (sk == "make_sand") {
                for (int t = 0; t < 100; ++t) {
                    Cell c{(int)(rng()() % b.n), (int)(rng()() % b.m)};
                    if (!sandOk(b, c)) continue;
                    Cell s = pickMove(b.phased);
                    if (s.first < 0) break;
                    json out = {{"skill", "make_sand"},
                                {"to", {c.first, c.second}},
                                {"action", {{"type", "move"}, {"to", {s.first, s.second}}}}};
                    std::cout << out.dump();
                    return 0;
                }
            } else if ((sk == "free_wall" || sk == "l_remodel") &&
                       (b.wallsLeft > 0 || b.freeWall)) {
                bool allowL = sk == "l_remodel" || b.lBonus > 0;
                auto cands = legalWallCands(b, allowL);
                // l_remodel 现打现放：只选 L 墙；free_wall：只选直墙
                std::vector<WallSpec> fit;
                for (auto &w : cands)
                    if ((sk == "l_remodel") == (w.kind == std::string("L"))) fit.push_back(w);
                if (!fit.empty()) {
                    json out = {{"skill", sk},
                                {"action",
                                 {{"type", "wall"}, {"wall", wallJson(fit[rng()() % fit.size()])}}}};
                    std::cout << out.dump();
                    return 0;
                }
            }
            // 技能打不出则落到下面的普通行动
        }
    }

    // 25% 随机放墙（有存量或免费墙时），否则随机走子
    if ((b.wallsLeft > 0 || b.freeWall) && rng()() % 100 < 25) {
        auto cands = legalWallCands(b, b.lBonus > 0);
        std::vector<WallSpec> straights;
        for (auto &w : cands)
            if (w.kind == "straight") straights.push_back(w);
        if (!straights.empty()) {
            const WallSpec &w = straights[rng()() % straights.size()];
            if (b.freeWall && b.hand.count("free_wall") && b.hand["free_wall"] > 0 &&
                !b.seqSkillUsed) {
                json out = {{"skill", "free_wall"},
                            {"action", {{"type", "wall"}, {"wall", wallJson(w)}}}};
                std::cout << out.dump();
                return 0;
            }
            std::cout << json({{"type", "wall"}, {"wall", wallJson(w)}}).dump();
            return 0;
        }
    }
    Cell s = pickMove(b.phased);
    if (s.first < 0) s = b.me;
    std::cout << json({{"type", "move"}, {"to", {s.first, s.second}}}).dump();
    return 0;
}
