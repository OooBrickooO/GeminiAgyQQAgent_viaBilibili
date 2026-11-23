"""
log.py — 弥酱 QQAgent 分类与流式日志监控控制台

功能：
1. 一起看 (全量流): python log.py all 或 python log.py
2. 分类独立看:
   - python log.py think  (只看思维链心流、模型启停与耗时)
   - python log.py chat   (只看群聊与私聊消息、加压与防抖)
   - python log.py bili   (只看B站冲浪探索、打分与安利手账)
   - python log.py tool   (只看MCP工具执行与发信动作)
   - python log.py error  (只看告警、超时与异常报错)
   - python log.py deskpet (只看桌宠独立日志，不混入全局滚动流)
"""
import os
import sys
import time
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")

MODES = {
    "all": {
        "file": "all.log",
        "title": "🌟 全局混合流 (全部日志一起看)",
        "desc": "无过滤，原汁原味展示所有守护进程与模块日志"
    },
    "think": {
        "file": "thought.log",
        "title": "💭 思维心流 (弥酱思考脉搏与CoT)",
        "desc": "只看弥酱在想什么、思考耗时、模型启停与推理完成"
    },
    "thought": {
        "file": "thought.log",
        "title": "💭 思维心流 (别名)",
        "desc": "同 think"
    },
    "chat": {
        "file": "chat.log",
        "title": "📩 聊天与群交互 (消息/防抖/加压)",
        "desc": "只看群聊/私聊消息接收、防抖聚合与压力变化"
    },
    "bili": {
        "file": "bili.log",
        "title": "🏄 B站冲浪生态 (视频/打分/手账/安利)",
        "desc": "只看后台B站摸鱼探索、字幕热评抽取、打分与分享安利"
    },
    "tool": {
        "file": "tool.log",
        "title": "⚙️ 工具调用与动作执行 (MCP Tools)",
        "desc": "只看大模型实际调用的发信、发图、点赞、评论等动作"
    },
    "mcp": {
        "file": "tool.log",
        "title": "⚙️ 工具调用 (别名)",
        "desc": "同 tool"
    },
    "error": {
        "file": "error.log",
        "title": "⚠️ 告警与异常 (错误/超时/风控拦截)",
        "desc": "只看异常报错、安全拦截、超时斩首与故障信息"
    },
    "deskpet": {
        "file": "deskpet.log",
        "title": "🐾 桌宠专属日志 (DeskPet / desktop_master)",
        "desc": "桌宠事件、看屏吐槽、pet_* 工具与桌宠回合推理输出（不进入全局滚动日志）"
    },
    "pet": {
        "file": "deskpet.log",
        "title": "🐾 桌宠专属日志 (别名)",
        "desc": "同 deskpet"
    }
}


def print_banner():
    print("\033[1;36m========================================================\033[0m")
    print("\033[1;35m       🐱 弥酱 QQAgent 多通道分类日志查看器 🐾\033[0m")
    print("\033[1;36m========================================================\033[0m")
    print("支持模式:")
    for k, v in MODES.items():
        if k in ("thought", "mcp", "pet"):
            continue
        print(f"  \033[1;32mpython log.py {k:<6}\033[0m -> {v['title']}")
    print("\033[1;36m========================================================\033[0m\n")


def follow_file(filepath: str, n_lines: int = 30):
    """类似 tail -n N -f 实时跟踪文件"""
    if not os.path.exists(filepath):
        print(f"\033[33m⏳ 日志文件 {filepath} 尚未产生，正在等待守护进程写入...\033[0m")
        while not os.path.exists(filepath):
            time.sleep(0.5)

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        # 先读取最后 n_lines 行
        all_lines = f.readlines()
        tail_lines = all_lines[-n_lines:] if len(all_lines) > n_lines else all_lines
        for line in tail_lines:
            sys.stdout.write(line)
        sys.stdout.flush()

        # 实时跟随
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                time.sleep(0.3)


def main():
    parser = argparse.ArgumentParser(description="弥酱日志查看器", add_help=False)
    parser.add_argument("mode", nargs="?", default="all", help="日志模式: all | think | chat | bili | tool | error | deskpet")
    parser.add_argument("-n", "--lines", type=int, default=30, help="初始展示行数 (默认30)")
    parser.add_argument("-h", "--help", action="store_true", help="显示帮助信息")

    args = parser.parse_args()

    if args.help or args.mode in ("-h", "--help", "help"):
        print_banner()
        return

    mode_key = args.mode.lower().strip()
    if mode_key not in MODES:
        print(f"\033[31m❌ 未知模式 '{args.mode}'！\033[0m")
        print_banner()
        return

    info = MODES[mode_key]
    target_file = os.path.join(LOGS_DIR, info["file"])

    print(f"\033[1;32m>>> 正在实时跟踪: {info['title']} ({target_file}) <<<\033[0m")
    print(f"\033[90m(提示: 按 Ctrl+C 随时退出当前查看)\033[0m\n")

    try:
        follow_file(target_file, n_lines=args.lines)
    except KeyboardInterrupt:
        print("\n\033[33m👋 已退出日志查看。\033[0m")
    except Exception as e:
        print(f"\n\033[31m⚠️ 读取日志异常: {e}\033[0m")


if __name__ == "__main__":
    main()

