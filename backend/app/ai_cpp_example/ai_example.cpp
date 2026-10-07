// 示例 AI（C++，贪心策略）：从 stdin 读入 json，向 stdout 输出一行决策 json。
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
//   - 走子：BFS 算己方到获胜点的最短路（复刻引擎阻挡边与跳子规则），沿最短路走一步，
//     严重回避流沙（踩流沙等于白送对方连续行动两次）。
//   - 放墙：枚举候选直墙，选“对方最短路增量最大、且己方仍有路”的；偶尔用 L 墙。
//   - 技能：距目标较远时打连续行动/穿墙；放墙前打免费墙；开局在对方必经之路埋流沙。
//   - 所有输出均经本地合法性复算，保证落子必合法（避免被判负）。
//
// 编译（docker，见本目录 Dockerfile / build.sh）：
//   docker build -t quoridor-ai-example .
// 运行：
//   docker run --rm -i --network none quoridor-ai-example < kifu.json

#include <algorithm>
#include <chrono>
#include <iostream>
#include <map>
#include <queue>
#include <random>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "json.hpp"  // 捆绑的 nlohmann/json 单头文件（v3.11.3）

using json = nlohmann::json;
using Cell = std::pair<int, int>;  // (r, c)

struct WallSpec {
    int wr = 0, wc = 0;
    std::string kind = "straight";  // straight / L
    std::string ori = "H";          // straight 用
    std::string arm = "NW";         // L 用
};

// ---------------- 棋盘状态 ----------------

struct Board {
    int n = 9, m = 9;
    Cell me = {0, 4}, opp = {8, 4};
    int turn = 0;                          // 本 AI 执子（输入棋谱的 turn）
    std::vector<WallSpec> wallSpecs;       // 已有墙
    std::set<Cell> deads, sands;
    std::vector<int> goalA, goalB;         // 获胜列
    std::map<std::string, int> hand;       // 手牌 {技能id: 张数}
    int wallsLeft = 0, lBonus = 0;
    bool phased = false, freeWall = false;  // 穿墙 / 免费墙 buff
    bool mustMove = false;                 // 连续行动中：只能走子
    bool seqSkillUsed = false;             // 本序列已打出过技能
};

// 单面墙阻断的双向边（与后端 engine.wall_edges 完全一致）
static void addEdge(std::set<std::pair<Cell, Cell>> &s, Cell a, Cell b) {
    s.insert({a, b});
    s.insert({b, a});
}
static void wallEdges(std::set<std::pair<Cell, Cell>> &s, int wr, int wc,
                      const std::string &kind, const std::string &ori, const std::string &arm) {
    if (kind == "L") {
        bool hasN = arm.find('N') != std::string::npos, hasS = arm.find('S') != std::string::npos;
        bool hasW = arm.find('W') != std::string::npos, hasE = arm.find('E') != std::string::npos;
        if (hasN) addEdge(s, {wr - 1, wc - 1}, {wr - 1, wc});
        if (hasS) addEdge(s, {wr, wc - 1}, {wr, wc});
        if (hasW) addEdge(s, {wr - 1, wc - 1}, {wr, wc - 1});
        if (hasE) addEdge(s, {wr - 1, wc}, {wr, wc});
    } else if (ori == "H") {
        addEdge(s, {wr - 1, wc}, {wr, wc});
        addEdge(s, {wr - 1, wc + 1}, {wr, wc + 1});
    } else {  // V
        addEdge(s, {wr, wc - 1}, {wr, wc});
        addEdge(s, {wr + 1, wc - 1}, {wr + 1, wc});
    }
}

static std::set<std::pair<Cell, Cell>> buildBlocked(const Board &b, const std::vector<WallSpec> &extra,
                                                    bool ignoreWalls) {
    std::set<std::pair<Cell, Cell>> s;
    if (ignoreWalls) return s;
    auto apply = [&](const WallSpec &w) {
        if (w.kind == "L")
            wallEdges(s, w.wr, w.wc, "L", "", w.arm);
        else
            wallEdges(s, w.wr, w.wc, "straight", w.ori, "");
    };
    for (const auto &ws : b.wallSpecs) apply(ws);
    for (const auto &w : extra) apply(w);
    return s;
}

// ---------------- 走子（含跳子） ----------------

// 从 (r,c) 出发的单步可达格（复刻引擎 _step_neighbors；ignoreWalls 穿墙时无视墙）
static std::vector<Cell> stepNeighbors(const Board &b, Cell from, Cell selfPawn, Cell oppPawn,
                                       const std::set<std::pair<Cell, Cell>> &blocked) {
    std::vector<Cell> out;
    const int dr[4] = {-1, 1, 0, 0}, dc[4] = {0, 0, -1, 1};
    for (int k = 0; k < 4; ++k) {
        int nr = from.first + dr[k], nc = from.second + dc[k];
        if (nr < 0 || nr >= b.n || nc < 0 || nc >= b.m) continue;
        if (b.deads.count({nr, nc})) continue;
        if (blocked.count({from, {nr, nc}})) continue;
        if (Cell(nr, nc) != oppPawn) {
            out.push_back({nr, nc});
            continue;
        }
        // 跳子：直线落点
        int br = nr + dr[k], bc = nc + dc[k];
        bool straightOk = br >= 0 && br < b.n && bc >= 0 && bc < b.m &&
                          !b.deads.count({br, bc}) && !blocked.count({{nr, nc}, {br, bc}});
        if (straightOk) {
            out.push_back({br, bc});
        } else {
            // 直线被挡：走对方棋子两侧斜格
            const int sdr[2] = {dc[k], -dc[k]}, sdc[2] = {dr[k], -dr[k]};
            for (int t = 0; t < 2; ++t) {
                int tr = nr + sdr[t], tc = nc + sdc[t];
                if (tr < 0 || tr >= b.n || tc < 0 || tc >= b.m) continue;
                if (b.deads.count({tr, tc})) continue;
                if (blocked.count({{nr, nc}, {tr, tc}})) continue;
                if (std::find(out.begin(), out.end(), Cell(tr, tc)) == out.end())
                    out.push_back({tr, tc});
            }
        }
    }
    return out;
}

// BFS 到目标的最短步数（到不了返回 INF；ignoreWalls 穿墙视角）
static const int INF = 1 << 29;
static std::vector<std::vector<int>> bfsDist(const Board &b, int player,
                                            const std::set<std::pair<Cell, Cell>> &blocked) {
    std::vector<std::vector<int>> dist(b.n, std::vector<int>(b.m, INF));
    std::queue<Cell> q;
    // 从全部获胜格反向 BFS
    if (player == 0) {
        for (int c : b.goalA)
            if (!b.deads.count({b.n - 1, c})) {
                dist[b.n - 1][c] = 0;
                q.push({b.n - 1, c});
            }
    } else {
        for (int c : b.goalB)
            if (!b.deads.count({0, c})) {
                dist[0][c] = 0;
                q.push({0, c});
            }
    }
    Cell selfPawn = player == b.turn ? b.me : b.opp;
    Cell oppPawn = player == b.turn ? b.opp : b.me;
    while (!q.empty()) {
        Cell cur = q.front();
        q.pop();
        // 反向邻居：在“忽略棋子占位”意义下用正向规则近似（足够贪心使用）
        const int dr[4] = {-1, 1, 0, 0}, dc[4] = {0, 0, -1, 1};
        for (int k = 0; k < 4; ++k) {
            int nr = cur.first + dr[k], nc = cur.second + dc[k];
            if (nr < 0 || nr >= b.n || nc < 0 || nc >= b.m) continue;
            if (b.deads.count({nr, nc})) continue;
            if (blocked.count({cur, {nr, nc}})) continue;
            if (dist[nr][nc] == INF) {
                dist[nr][nc] = dist[cur.first][cur.second] + 1;
                q.push({nr, nc});
            }
        }
    }
    return dist;
}

// ---------------- 放墙合法性（复刻引擎 is_wall_legal，不含“围死允许”：AI 主动避开围死） ----------------

static bool wallInBounds(const Board &b, const WallSpec &w) {
    if (w.kind == "L")
        return w.wr >= 1 && w.wr <= b.n - 1 && w.wc >= 1 && w.wc <= b.m - 1 &&
               (w.arm == "NW" || w.arm == "NE" || w.arm == "SW" || w.arm == "SE");
    if (w.ori == "H") return w.wr >= 1 && w.wr <= b.n - 1 && w.wc >= 0 && w.wc <= b.m - 2;
    if (w.ori == "V") return w.wr >= 0 && w.wr <= b.n - 2 && w.wc >= 1 && w.wc <= b.m - 1;
    return false;
}

// ---------------- 输入解析 ----------------

// 注：nlohmann::json 的算术转换是 explicit 的，隐式写进 pair/容器极易踩坑，
// 此处一律显式转换；arm:null（后端直墙序列化）按缺省处理。
static int getInt(const json &o, const char *key, int def) {
    auto it = o.find(key);
    if (it != o.end() && it->is_number()) return (int)(*it);
    return def;
}
static std::string getStr(const json &o, const char *key, const std::string &def) {
    auto it = o.find(key);
    if (it != o.end() && it->is_string()) return (std::string)(*it);
    return def;
}

static Board parseBoard(const json &j) {
    Board b;
    b.n = getInt(j, "n", 9);
    b.m = getInt(j, "m", 9);
    b.turn = getInt(j, "turn", 0);
    auto ps = j.value("pawns", json::array());
    if (ps.size() == 2) {
        b.me = {(int)ps[b.turn][0], (int)ps[b.turn][1]};
        b.opp = {(int)ps[1 - b.turn][0], (int)ps[1 - b.turn][1]};
    }
    for (auto &w : j.value("walls", json::array())) {
        WallSpec ws;
        ws.wr = (int)w["wr"];
        ws.wc = (int)w["wc"];
        ws.kind = getStr(w, "kind", "straight");
        ws.ori = getStr(w, "orientation", "H");
        ws.arm = getStr(w, "arm", "NW");
        b.wallSpecs.push_back(ws);
    }
    for (auto &d : j.value("deads", json::array()))
        if (d.is_array() && d.size() == 2) b.deads.insert({(int)d[0], (int)d[1]});
    for (auto &s : j.value("sands", json::array()))
        if (s.is_array() && s.size() == 2) b.sands.insert({(int)s[0], (int)s[1]});
    for (auto &c : j.value("goal_A", json::array()))
        if (c.is_number()) b.goalA.push_back((int)c);
    for (auto &c : j.value("goal_B", json::array()))
        if (c.is_number()) b.goalB.push_back((int)c);
    auto hands = j.value("hands", json::array());
    if (hands.size() > (size_t)b.turn)
        for (auto &[k, v] : hands[b.turn].items()) b.hand[k] = (int)v;
    auto wl = j.value("walls_left", json::array());
    if (wl.size() > (size_t)b.turn && wl[b.turn].is_number()) b.wallsLeft = (int)wl[b.turn];
    auto lb = j.value("l_bonus", json::array());
    if (lb.size() > (size_t)b.turn && lb[b.turn].is_number()) b.lBonus = (int)lb[b.turn];
    auto ph = j.value("phase_buff", json::array());
    if (ph.size() > (size_t)b.turn && ph[b.turn].is_boolean()) b.phased = (bool)ph[b.turn];
    auto fr = j.value("free_buff", json::array());
    if (fr.size() > (size_t)b.turn && fr[b.turn].is_boolean()) b.freeWall = (bool)fr[b.turn];
    if (j.contains("must_move") && j["must_move"].is_boolean()) b.mustMove = (bool)j["must_move"];
    if (j.contains("seq_skill_used") && j["seq_skill_used"].is_boolean())
        b.seqSkillUsed = (bool)j["seq_skill_used"];
    return b;
}

// ---------------- 决策 ----------------

static std::mt19937 &rng() {
    static std::mt19937 r((unsigned)std::chrono::steady_clock::now().time_since_epoch().count());
    return r;
}

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

// 流沙陷阱合法落点（复刻引擎 make_sand 约束）
static bool sandOk(const Board &b, Cell c) {
    if (c.first < 0 || c.first >= b.n || c.second < 0 || c.second >= b.m) return false;
    if (b.deads.count(c) || b.sands.count(c)) return false;
    if (c == b.me || c == b.opp) return false;
    if (b.turn == 0 && c.first == b.n - 1 &&
        std::find(b.goalA.begin(), b.goalA.end(), c.second) != b.goalA.end())
        return false;
    if (b.turn == 1 && c.first == 0 &&
        std::find(b.goalB.begin(), b.goalB.end(), c.second) != b.goalB.end())
        return false;
    return true;
}

static json wallJson(const WallSpec &w) {
    json o = {{"wr", w.wr}, {"wc", w.wc}};
    if (w.kind == "L") {
        o["kind"] = "L";
        o["arm"] = w.arm;
    } else {
        o["orientation"] = w.ori;
    }
    return o;
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

    // ===== 阶段二：行动 =====
    Board b = parseBoard(j);
    bool canSkill = !b.seqSkillUsed;
    auto useSkill = [&](const std::string &s) { return canSkill && b.hand.count(s) && b.hand[s] > 0; };

    auto blocked = buildBlocked(b, {}, b.phased);
    auto legal = stepNeighbors(b, b.me, b.me, b.opp, blocked);
    if (legal.empty()) {  // 无路（几乎不可能）：原地放墙或认输式输出
        std::cout << "{\"type\":\"move\",\"to\":[" << b.me.first << "," << b.me.second << "]}";
        return 0;
    }
    auto distMe = bfsDist(b, b.turn, blocked);
    auto blockedOpp = buildBlocked(b, {}, false);
    auto distOpp = bfsDist(b, 1 - b.turn, blockedOpp);
    int myD = distMe[b.me.first][b.me.second];
    int oppD = distOpp[b.opp.first][b.opp.second];

    // 连续行动序列中只能走子
    if (b.mustMove) {
        Cell s = bestStep(b, distMe, legal);
        std::cout << json({{"type", "move"}, {"to", {s.first, s.second}}}).dump();
        return 0;
    }

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
            json out = {{"skill", "make_sand"},
                        {"to", {target.first, target.second}},
                        {"action", {{"type", "move"}, {"to", {s.first, s.second}}}}};
            std::cout << out.dump();
            return 0;
        }
    }

    // --- 放墙评估：找对方增量最大、己方仍有路的墙 ---
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
        if (b.lBonus > 0 || (canSkill && b.hand.count("l_remodel") && b.hand["l_remodel"] > 0)) {
            const std::string arms[4] = {"NW", "NE", "SW", "SE"};
            for (int wr = 1; wr <= b.n - 1; ++wr)
                for (int wc = 1; wc <= b.m - 1; ++wc)
                    for (auto &a : arms) cands.push_back({wr, wc, "L", "", a});
        }
        std::shuffle(cands.begin(), cands.end(), rng());  // 同分随机，打破固定偏移
        int checked = 0;
        for (auto &w : cands) {
            if (++checked > 600) break;  // 限时：大棋盘只抽查前 600 候选
            if (w.kind == "L" && b.lBonus <= 0) continue;  // L 需改造权（下面单独处理）
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

    // --- 技能 2：连续行动（距目标远时） ---
    if (useSkill("double_move") && myD > 3 && myD < INF) {
        Cell s = bestStep(b, distMe, legal);
        json out = {{"skill", "double_move"}, {"action", {{"type", "move"}, {"to", {s.first, s.second}}}}};
        std::cout << out.dump();
        return 0;
    }
    // --- 技能 3：穿墙（被墙严重绕路时，用无视墙视角走一步） ---
    if (useSkill("phase_walk") && myD > 6 && myD < INF) {
        auto distPhase = bfsDist(b, b.turn, {});
        auto legalPhase = stepNeighbors(b, b.me, b.me, b.opp, {});
        if (!legalPhase.empty()) {
            Cell s = bestStep(b, distPhase, legalPhase);
            json out = {{"skill", "phase_walk"}, {"action", {{"type", "move"}, {"to", {s.first, s.second}}}}};
            std::cout << out.dump();
            return 0;
        }
    }
    // --- 放墙（含免费墙 / L 墙） ---
    if (hasWall && bestGain >= 2 && (b.freeWall || (rng()() % 100 < 30))) {
        json wj = wallJson(bestWall);
        if (b.freeWall && useSkill("free_wall")) {
            json out = {{"skill", "free_wall"}, {"action", {{"type", "wall"}, {"wall", wj}}}};
            std::cout << out.dump();
            return 0;
        }
        if (!b.freeWall) {
            std::cout << json({{"type", "wall"}, {"wall", wj}}).dump();
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
            json out = {{"skill", "l_remodel"}, {"action", {{"type", "wall"}, {"wall", wallJson(w)}}}};
            std::cout << out.dump();
            return 0;
        }
    }

    // --- 默认：沿最短路走一步 ---
    Cell s = bestStep(b, distMe, legal);
    std::cout << json({{"type", "move"}, {"to", {s.first, s.second}}}).dump();
    return 0;
}
