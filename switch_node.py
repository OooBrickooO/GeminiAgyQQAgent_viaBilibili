import urllib.request
import json
import urllib.parse
import sys
import os

BASE_URL = "http://127.0.0.1:9090/proxies"
HEADERS = {"Authorization": f"Bearer {os.environ.get('MIHOMO_SECRET', 'YOUR_MIHOMO_SECRET')}"}

def get_proxies():
    req = urllib.request.Request(BASE_URL, headers=HEADERS)
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode('utf-8'))['proxies']

def switch_node(group_name, node_name):
    url = f"{BASE_URL}/{urllib.parse.quote(group_name)}"
    data = json.dumps({"name": node_name}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=HEADERS, method='PUT')
    with urllib.request.urlopen(req) as response:
        return response.status

try:
    proxies = get_proxies()
    # 找出所有允许手动选节点的策略组
    selectors = [k for k, v in proxies.items() if v['type'] == 'Selector']
    
    if not selectors:
        print("没有找到策略组，可能是配置格式问题。")
        sys.exit(0)
        
    print("\n=========== [命令行节点切换器] ===========")
    for i, name in enumerate(selectors):
        now = proxies[name].get('now', '未知')
        print(f"[{i}] {name} (当前正在使用: {now})")
    
    sel_idx = input("\n请输入要操作的策略组编号 (通常选0, 直接回车默认0): ").strip()
    sel_idx = int(sel_idx) if sel_idx else 0
    group_name = selectors[sel_idx]
    
    # 拿到该策略组下的所有节点
    all_nodes = proxies[group_name]['all']
    
    # 为了方便，优先筛选美国/新加坡节点，如果没有就显示全部
    target_nodes = [n for n in all_nodes if '美' in n or 'US' in n.upper() or '新' in n or 'SG' in n.upper() or '狮城' in n]
    if not target_nodes:
        target_nodes = all_nodes
        
    print(f"\n--- [{group_name}] 可用的优质节点 ---")
    for i, n in enumerate(target_nodes):
        print(f"[{i}] {n}")
        
    node_idx = input("\n请输入你要切换到的节点编号: ").strip()
    node_name = target_nodes[int(node_idx)]
    
    switch_node(group_name, node_name)
    print(f"\n🎉 完美！已经将策略组 [{group_name}] 强行切换为节点：[{node_name}]")
    print("现在节点绝对安全了，去跑 Antigravity 安装命令吧！")
    
except urllib.error.URLError as e:
    print("网络连接失败，请确认 mihomo 正在运行并且端口是 9090！", e)
except Exception as e:
    print("发生错误:", e)
