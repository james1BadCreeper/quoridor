# 示例 AI 的编译镜像：容器内编译，运行期无网络。
# 作者自己的 AI 照此模式写 Dockerfile 即可（COPY 源码 → g++ 编译 → ENTRYPOINT 为二进制）。
FROM gcc:14-bookworm
WORKDIR /ai
COPY ai_example.cpp json.hpp ./
RUN g++ -std=c++17 -O2 -o /ai/ai ai_example.cpp
ENTRYPOINT ["/ai/ai"]
