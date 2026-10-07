// 作者公共库：棋盘解析、阻挡边、走子（含跳子）、BFS、放墙/流沙合法性复算。
// 用法：#include "ai_common.hpp"，与 json.hpp 同目录即可。
// 注意：nlohmann::json 的算术转换是 explicit 的，此处一律显式转换，照抄即可。

#ifndef QUORIDOR_AI_COMMON_HPP
#define QUORIDOR_AI_COMMON_HPP

#include <algorithm>
#include <chrono>
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
    int turn = 0;                     // 本 AI 执子（输入棋谱的 turn）
    std::vector<WallSpec> wallSpecs;  // 已有墙
    std::set<Cell> deads, sands;
    std::vector<int> goalA, goalB;         // 获胜列
    std::map<std::string, int> hand;      // 自家手牌 {技能id: 张数}
    int oppHandCount = 0;                 // 对方剩余手牌总数（只给张数，不给明细）
    int wallsLeft = 0, lBonus = 0;
    bool phased = false, freeWall = false;  // 穿墙 / 免费墙 buff
    bool mustMove = false;                  // 连续行动中：只能走子
    bool seqSkillUsed = false;              // 本序列已打出过技能
    int bonusMoves = 0;                     // 本轮剩余步数（连续行动/流沙罚步）
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
    (void)selfPawn;
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

// BFS 到目标的最短步数（到不了返回 INF）
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
    while (!q.empty()) {
        Cell cur = q.front();
        q.pop();
        // 反向邻居：在“忽略棋子占位”意义下用正向规则近似（足够启发式使用）
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

// ---------------- 放墙合法性（复刻引擎 is_wall_legal；AI 应再自行保证不断路） ----------------

static bool wallInBounds(const Board &b, const WallSpec &w) {
    if (w.kind == "L")
        return w.wr >= 1 && w.wr <= b.n - 1 && w.wc >= 1 && w.wc <= b.m - 1 &&
               (w.arm == "NW" || w.arm == "NE" || w.arm == "SW" || w.arm == "SE");
    if (w.ori == "H") return w.wr >= 1 && w.wr <= b.n - 1 && w.wc >= 0 && w.wc <= b.m - 2;
    if (w.ori == "V") return w.wr >= 0 && w.wr <= b.n - 2 && w.wc >= 1 && w.wc <= b.m - 1;
    return false;
}

// ---------------- 输入解析 ----------------

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
        ws.arm = getStr(w, "arm", "NW");  // arm:null（后端直墙序列化）按缺省处理
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
    auto ho = j.value("hand", json::object());
    if (ho.is_object())
        for (auto &[k, v] : ho.items())
            if (v.is_number()) b.hand[k] = (int)v;
    b.oppHandCount = getInt(j, "opp_hand_count", 0);
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
    b.bonusMoves = getInt(j, "bonus_moves", 0);
    return b;
}

// ---------------- 决策 ----------------

static std::mt19937 &rng() {
    static std::mt19937 r((unsigned)std::chrono::steady_clock::now().time_since_epoch().count());
    return r;
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

// ---------------- 整轮决策校验（与后端落子语义一致，输出前调用） ----------------

struct DecisionCheck {
    bool ok = false;
    std::string reason;  // ok=false 时为中文原因，可直接打日志
};

// 是否到达己方获胜点
static bool isGoal(const Board &b, int player, Cell c) {
    if (player == 0)
        return c.first == b.n - 1 &&
               std::find(b.goalA.begin(), b.goalA.end(), c.second) != b.goalA.end();
    return c.first == 0 && std::find(b.goalB.begin(), b.goalB.end(), c.second) != b.goalB.end();
}

// 双方是否都有路（围死即终局）
static bool bothHavePath(const Board &b) {
    auto blocked = buildBlocked(b, {}, false);
    return bfsDist(b, b.turn, blocked)[b.me.first][b.me.second] < INF &&
           bfsDist(b, 1 - b.turn, blocked)[b.opp.first][b.opp.second] < INF;
}

// 从行动 json 提取走子落点（形状非法返回 false）
static bool getMoveTo(const json &a, Cell &to) {
    auto it = a.find("to");
    if (it == a.end() || !it->is_array() || it->size() != 2 || !(*it)[0].is_number() ||
        !(*it)[1].is_number())
        return false;
    to = {(int)(*it)[0], (int)(*it)[1]};
    return true;
}

// 从行动 json 提取墙（形状非法返回 false；直墙 orientation 缺失/错误也算非法，与后端一致）
static bool getActionWall(const json &a, WallSpec &w) {
    auto it = a.find("wall");
    if (it == a.end() || !it->is_object()) return false;
    const json &o = *it;
    auto iwr = o.find("wr"), iwc = o.find("wc");
    if (iwr == o.end() || iwc == o.end() || !iwr->is_number() || !iwc->is_number()) return false;
    w.wr = (int)*iwr;
    w.wc = (int)*iwc;
    w.kind = getStr(o, "kind", "straight");
    if (w.kind == "L") {
        w.arm = getStr(o, "arm", "");
        if (w.arm != "NW" && w.arm != "NE" && w.arm != "SW" && w.arm != "SE") return false;
    } else if (w.kind == "straight") {
        w.ori = getStr(o, "orientation", "");
        if (w.ori != "H" && w.ori != "V") return false;
    } else {
        return false;
    }
    return true;
}

// 校验整轮决策（输出前调用，不过直接判负——与后端落子语义逐条一致）：
//   形状：对象，actions 为 1~2 个行动对象；
//   技能：须在手牌、序列新鲜，make_sand 须带合法 to；
//   首行动按打出后状态校验；若首行动终结轮次（获胜/围死/踩流沙/换人），
//   后续 actions 作废不再校验；否则在落子后状态下继续校验第二个行动。
// 注意围死墙按引擎语义合法（此处不断路强制）。
static DecisionCheck checkDecision(const Board &b, const json &d) {
    DecisionCheck r;
    if (!d.is_object()) {
        r.reason = "决策须为 json 对象";
        return r;
    }
    auto ait = d.find("actions");
    if (ait == d.end() || !ait->is_array() || ait->empty() || ait->size() > 2) {
        r.reason = "actions 须为 1~2 个行动对象";
        return r;
    }
    // 技能预检（只读，不真正扣牌）
    Board s = b;
    auto skillIt = d.find("skill");
    if (skillIt != d.end()) {
        if (!skillIt->is_string()) {
            r.reason = "skill 须为字符串";
            return r;
        }
        std::string sk = *skillIt;
        if (b.seqSkillUsed) {
            r.reason = "本序列已打出过技能";
            return r;
        }
        auto hc = s.hand.find(sk);
        if (hc == s.hand.end() || hc->second <= 0) {
            r.reason = "手牌中无此技能:" + sk;
            return r;
        }
        if (sk == "double_move") {
            s.bonusMoves += 1;
            s.mustMove = true;
        } else if (sk == "phase_walk") {
            s.phased = true;
        } else if (sk == "free_wall") {
            s.freeWall = true;
        } else if (sk == "l_remodel") {
            s.lBonus += 1;
        } else if (sk == "make_sand") {
            auto toIt = d.find("to");
            if (toIt == d.end() || !toIt->is_array() || toIt->size() != 2 ||
                !(*toIt)[0].is_number() || !(*toIt)[1].is_number()) {
                r.reason = "make_sand 须带 to 落点";
                return r;
            }
            Cell c{(int)(*toIt)[0], (int)(*toIt)[1]};
            if (!sandOk(b, c)) {
                r.reason = "流沙落点非法";
                return r;
            }
            s.sands.insert(c);
        } else {
            r.reason = "未知技能:" + sk;
            return r;
        }
    }
    // 逐个行动校验（本地仿真）
    int remaining = s.bonusMoves;
    for (auto &a : *ait) {
        if (!a.is_object()) {
            r.reason = "行动须为对象";
            return r;
        }
        std::string t = getStr(a, "type", "");
        if (t == "move") {
            Cell to{-1, -1};
            if (!getMoveTo(a, to)) {
                r.reason = "走子须带 to:[r,c]";
                return r;
            }
            auto legal = stepNeighbors(s, s.me, s.me, s.opp, buildBlocked(s, {}, s.phased));
            if (std::find(legal.begin(), legal.end(), to) == legal.end()) {
                r.reason = "非法走子";
                return r;
            }
            s.me = to;
            if (isGoal(s, s.turn, to) || s.sands.count(to)) break;  // 获胜/踩流沙：轮次终结
        } else if (t == "wall") {
            if (s.mustMove) {
                r.reason = "连续行动中只能走子";
                return r;
            }
            WallSpec w;
            if (!getActionWall(a, w)) {
                r.reason = "放墙须带合法 wall 对象";
                return r;
            }
            // 余墙/L 券/范围/重边（围死按引擎语义合法，不断路不强制）
            if (w.kind == "L" && s.lBonus <= 0) {
                r.reason = "无 L 墙放置权（L 须配改造同打）";
                return r;
            }
            if (!s.freeWall && s.wallsLeft <= 0) {
                r.reason = "无剩余墙";
                return r;
            }
            if (!wallInBounds(s, w)) {
                r.reason = "墙位置越界";
                return r;
            }
            std::set<std::pair<Cell, Cell>> e;
            wallEdges(e, w.wr, w.wc, w.kind, w.ori, w.arm);
            auto existing = buildBlocked(s, {}, false);
            bool overlap = false;
            for (auto &x : e)
                if (existing.count(x)) {
                    overlap = true;
                    break;
                }
            if (overlap) {
                r.reason = "与已有墙重叠";
                return r;
            }
            s.wallSpecs.push_back(w);
            if (w.kind == "L") s.lBonus -= 1;
            if (s.freeWall)
                s.freeWall = false;
            else
                s.wallsLeft -= 1;
            if (!bothHavePath(s)) break;  // 围死：轮次终结（被围者胜）
        } else {
            r.reason = "未知行动类型";
            return r;
        }
        // 本行动未终结轮次：有剩余步数则继续，否则换人、后续作废
        if (remaining > 0) {
            remaining--;
        } else {
            break;
        }
    }
    r.ok = true;
    return r;
}

// 组装整轮输出：技能（可空）+ 1~2 个行动，避免手写多层括号出错
static json moveAct(int r, int c) {
    json o = json::object();
    o["type"] = "move";
    o["to"] = {r, c};
    return o;
}
static json wallAct(const json &w) {
    json o = json::object();
    o["type"] = "wall";
    o["wall"] = w;
    return o;
}
static json decision(const std::string &skill, const json &skillTo, const std::vector<json> &acts) {
    json out = json::object();
    if (!skill.empty()) out["skill"] = skill;
    if (!skillTo.is_null()) out["to"] = skillTo;
    out["actions"] = json::array();
    for (auto &a : acts) out["actions"].push_back(a);
    return out;
}

// 带自检的输出：校验不过则退化为首个合法走子（无路时原样输出，实战中对局已结束）
static json checked(const Board &b, const json &out, const std::vector<Cell> &legal) {
    if (checkDecision(b, out).ok) return out;
    if (!legal.empty()) {
        Cell s = legal[0];
        return decision("", json(nullptr), {moveAct(s.first, s.second)});
    }
    return out;
}

// ---------------- 动作合法性总检（复刻引擎 apply_* 校验） ----------------

struct ActionCheck {
    bool ok = false;
    std::string reason;  // ok=false 时为中文原因，可直接打日志
};

// 走子是否合法：在合法走子集内（含跳子；穿墙 buff 下按无视墙视角）。
static ActionCheck checkMove(const Board &b, Cell to) {
    ActionCheck r;
    auto legal = stepNeighbors(b, b.me, b.me, b.opp, buildBlocked(b, {}, b.phased));
    if (std::find(legal.begin(), legal.end(), to) == legal.end()) {
        r.reason = "非法走子（不在合法走子集内：可能撞墙/进死点/停留对方格）";
        return r;
    }
    r.ok = true;
    return r;
}

// 放墙是否合法：余墙（免费墙除外）、范围内、不与已有墙重边。
// L 墙现打现放：lBonus 只在打出改造后的本次行动内有效，裸 L 输出一律非法；
// 配技能校验时把待放状态的 lBonus 置 1 再调本函数。
// forbidSurround=true 时还要求放墙后双方仍有路：
//   引擎本身允许围死（被围者按规则 4 直接获胜），所以该检查只是 AI 自保；
//   若“执意”围死（如算清对方被围后自己仍能赢），传 false 跳过此项即可。
static ActionCheck checkWall(const Board &b, const WallSpec &w, bool forbidSurround = true) {
    ActionCheck r;
    if (w.kind == "L" && b.lBonus <= 0) {
        r.reason = "无 L 墙放置权（需先打出改造技能）";
        return r;
    }
    if (!b.freeWall && b.wallsLeft <= 0) {
        r.reason = "该玩家无剩余墙";
        return r;
    }
    if (!wallInBounds(b, w)) {
        r.reason = "墙位置越界";
        return r;
    }
    std::set<std::pair<Cell, Cell>> e;
    wallEdges(e, w.wr, w.wc, w.kind, w.ori, w.arm);
    if (e.empty()) {
        r.reason = "未知墙类型";
        return r;
    }
    auto existing = buildBlocked(b, {}, false);
    for (auto &x : e)
        if (existing.count(x)) {
            r.reason = "与已有墙重叠（复用边）";
            return r;
        }
    if (forbidSurround) {
        auto nb = buildBlocked(b, {w}, false);
        if (bfsDist(b, b.turn, nb)[b.me.first][b.me.second] >= INF) {
            r.reason = "放墙后己方无路（会被围死而对方直接获胜）";
            return r;
        }
        if (bfsDist(b, 1 - b.turn, nb)[b.opp.first][b.opp.second] >= INF) {
            r.reason = "放墙后对方无路（对方被围死将直接获胜；执意围死可关掉 forbidSurround）";
            return r;
        }
    }
    r.ok = true;
    return r;
}

#endif  // QUORIDOR_AI_COMMON_HPP
