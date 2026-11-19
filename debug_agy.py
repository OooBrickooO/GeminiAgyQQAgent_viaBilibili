import os
import subprocess
import uuid

from config import AGY_EXECUTABLE, DEFAULT_AGY_EFFORT, MASTER_QQ, get_agy_env, build_agy_model_flags, BASE_DIR

# 统一获取经过配置的环境变量（包含代理与内网白名单）
env = get_agy_env()

chat_id_str = f"private_{MASTER_QQ}"
dynamic_conv_id = str(uuid.uuid5(uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8"), chat_id_str))

prompt = "测试一下你的调用工具能力，收到消息 aaa。请直接使用 send_private_msg 工具回复我 aaa。"

model_flags, _, _ = build_agy_model_flags(effort=DEFAULT_AGY_EFFORT, allow_low=False)
cmd = [
    AGY_EXECUTABLE,
    f"--conversation={dynamic_conv_id}"
]
cmd.extend(model_flags)
cmd.extend([
    "--dangerously-skip-permissions",
    "--prompt", prompt
])

print("🚀 开始启动大模型...")
print(f"命令: {' '.join(cmd)}")

try:
    # 强制让它在前台跑，把 stdout 和 stderr 实时原样输出到屏幕
    process = subprocess.Popen(cmd, env=env, cwd=BASE_DIR)
    process.wait()
    print("✅ 大模型执行完毕！")
except Exception as e:
    print(f"❌ 出现异常: {e}")
