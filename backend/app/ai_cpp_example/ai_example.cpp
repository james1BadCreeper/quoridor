// 示例 AI（C++）：从 stdin 读入当前棋谱 json，向 stdout 输出决策 json。
// 协议：
//   输入：与 GET /api/games/{id}/export 相同的棋谱对象（含 n, m, pawns, walls,
//         deads, sands, goal_A, goal_B, turn 等）。
//   输出（一行 json）：
//     走子：{"type":"move","to":[r,c]}
//     放直墙：{"type":"wall","wall":{"kind":"straight","wr":..,"wc":..,"orientation":"H"}} 
//     放L墙：{"type":"wall","wall":{"kind":"L","wr":..,"wc":..,"arm":"NW"}}
// 打包：把本文件及依赖打成 .zip 上传即可。本示例为随机策略，仅演示协议。
//
// 编译运行示例：
//   g++ -std=c++17 -O2 -o ai_example ai_example.cpp
//   python3 -c "import json;print(json.load(open('kifu.json')))" | ./ai_example
//
// 注意：为保持单文件可编译，本示例用极简字符串扫描解析棋盘尺寸与轮到谁，
// 实际参赛 AI 请接入完整 json 库（如 nlohmann/json）做合法性判断。

#include <bits/stdc++.h>
using namespace std;

// 从整段输入中提取所有整数
static vector<long long> scanInts(const string &s) {
    vector<long long> out;
    long long cur = 0; bool neg = false, in = false;
    for (char ch : s) {
        if (ch == '-' ) { neg = true; in = true; cur = 0; continue; }
        if (isdigit(ch)) { if (!in) { in = true; neg = false; cur = 0; } cur = cur * 10 + (ch - '0'); }
        else { if (in) { out.push_back(neg ? -cur : cur); in = false; } }
    }
    if (in) out.push_back(neg ? -cur : cur);
    return out;
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    string input((istreambuf_iterator<char>(cin)), istreambuf_iterator<char>());
    if (input.empty()) { cout << "{\"type\":\"move\",\"to\":[1,4]}"; return 0; }

    // 极简处理：找 "n":.. "m":.. "turn":..（找不到则给默认值）
    long long n = 9, m = 9, turn = 0;
    auto posN = input.find("\"n\"");
    auto posM = input.find("\"m\"");
    auto posT = input.find("\"turn\"");
    if (posN != string::npos) n = scanInts(input.substr(posN, 16))[0];
    if (posM != string::npos) m = scanInts(input.substr(posM, 16))[0];
    if (posT != string::npos) turn = scanInts(input.substr(posT, 20))[0];
    (void)turn;

    mt19937 rng((unsigned)chrono::steady_clock::now().time_since_epoch().count());
    // 随机尝试：50% 走子（向目标行走一步），50% 放横墙
    if (rng() % 2 == 0) {
        long long r = rng() % n, c = rng() % m;
        cout << "{\"type\":\"move\",\"to\":[" << r << "," << c << "]}";
    } else {
        long long wr = 1 + rng() % (n - 1), wc = rng() % (m - 1);
        cout << "{\"type\":\"wall\",\"wall\":{\"kind\":\"straight\",\"wr\":" << wr
             << ",\"wc\":" << wc << ",\"orientation\":\"H\"}}";
    }
    return 0;
}
