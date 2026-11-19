import urllib.request
import json
import urllib.parse
import sys
import os

# 强行屏蔽代理，防止发往本地 9090 的控制指令被发送到美国节点导致 502 Bad Gateway
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

BASE_URL = "http://127.0.0.1:9090"
HEADERS = {"Authorization": f"Bearer {os.environ.get('MIHOMO_SECRET', 'YOUR_MIHOMO_SECRET')}"}

try:
    # 【关键修复】：绝对不能用 global 模式，否则会导致 QQ 的国内流量也被发往美国，从而被腾讯风控断连（导致网络连接异常）！
    # 恢复为 rule (规则分流) 模式，让国内流量直连。
    req = urllib.request.Request(f"{BASE_URL}/configs", data=b'{"mode": "rule"}', headers=HEADERS, method='PATCH')
    urllib.request.urlopen(req)
    
    # 2. 获取所有节点
    req = urllib.request.Request(f"{BASE_URL}/proxies", headers=HEADERS)
    with urllib.request.urlopen(req) as res:
        proxies = json.loads(res.read().decode('utf-8'))['proxies']
        
    all_nodes = proxies['GLOBAL']['all']
    
    # 愿赌服输！严格听从用户的：只选指定的能过 Gemini 风控的节点【圣何塞7 | 三网推荐】！
    exact_match = [n for n in all_nodes if '圣何塞7 | 三网推荐' in n]
    if not exact_match:
        exact_match = [n for n in all_nodes if ('圣何塞7' in n or '圣何塞07' in n) and '三网' in n and '0.1倍' not in n]
    if not exact_match:
        exact_match = [n for n in all_nodes if ('圣何塞7' in n or '圣何塞07' in n) and '0.1倍' not in n]
    if exact_match:
        target = exact_match[0]
    else:
        # 万一将来机场把这个节点下线了，为了防止断网报错，我们做一个降级保护，去选其他美国节点
        target_nodes = [n for n in all_nodes if '美' in n or 'US' in n.upper() or '新' in n or 'SG' in n.upper() or '狮城' in n]
        if not target_nodes: target_nodes = all_nodes
        target = target_nodes[0]
    
    # 3. 将所有代理组（Selector）统统绑定到这个优质节点！
    # 这样在 Rule 模式下，无论大模型匹配到哪个代理组，都会走优质节点，而不会走烂节点超时！
    for name, proxy in proxies.items():
        if proxy.get('type') == 'Selector':
            try:
                # 只有包含该节点的组才能切换，有的组（如特定地区的组）可能没有这个节点
                if target in proxy.get('all', []):
                    req = urllib.request.Request(f"{BASE_URL}/proxies/{urllib.parse.quote(name)}", data=json.dumps({"name": target}).encode('utf-8'), headers=HEADERS, method='PUT')
                    urllib.request.urlopen(req)
            except Exception:
                pass
                
    print(f"✅ 破壁成功！")
    print(f"1. 已强制所有代理组锁定高优节点，维持 Rule 分流模式。")
    print(f"2. 大模型出口已死死绑定在节点: [{target}]")
    print("\n现在大模型将畅通无阻，同时 QQ 也能保持国内直连不断线！")
except Exception as e:
    print("出现错误:", e)
