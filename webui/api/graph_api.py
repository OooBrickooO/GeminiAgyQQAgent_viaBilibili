"""
webui/api/graph_api.py — 认知与社交引力网络拓扑 API (自然离散与全关系链驱动)
特性：
  1. 真实群号直显 (如: 群 YOUR_GROUP_ID_1、群 YOUR_GROUP_ID_2)；
  2. 纯净昵称清洗 (彻底移除末尾截断的括号与长QQ)；
  3. 私聊直连弥酱 (私聊过的群友拥有直连弥酱的专属光纤，同时保留所属群连线)；
  4. 扩容至全量活跃群友 (80~120人规模，不再过度裁剪)；
  5. 废除刻板轮辐圆环坐标，依托纯粹力导向算法自然离散延展。
"""
import os
import re
import json
import sqlite3
import collections
from webui.config import (
    MASTER_QQ,
    BOT_QQ,
    PROFILES_FILE,
    LONG_TERM_DB_FILE,
    ALLOWED_GROUPS_FILE
)


def clean_nickname(raw_name: str, uid_str: str) -> str:
    """清洗昵称：剔除末尾截断的括号与数字，确保节点标签纯净"""
    if not raw_name:
        return f"群友-{uid_str[-4:]}"
    # 剔除 (123456) 或未闭合的 (12345...
    name = re.sub(r'[\(（\[【].*$', '', raw_name).strip()
    if not name or name.startswith("群友 1") or name.startswith("群友 2") or name.startswith("群友 3"):
        return f"群友-{uid_str[-4:]}"
    return name[:10]


def get_mind_graph_data() -> dict:
    nodes = []
    links = []
    categories = [
        {"name": "星见弥 (核心)"},
        {"name": "👑 主人专属空间"},
        {"name": "👥 QQ群聊枢纽"},
        {"name": "💬 私聊互动对象"},
        {"name": "💖 挚友圈"},
        {"name": "🤝 朋友稳态区"},
        {"name": "✨ 活跃群友"},
        {"name": "👤 人物画像"},
        {"name": "🎉 群记忆/群梗"}
    ]

    master_str = str(MASTER_QQ)

    # 1. 深度扫描数据库：提取所有群聊、私聊用户与真实群名片
    user_groups_map = collections.defaultdict(set)
    user_nicknames_map = {}
    private_chat_users = {master_str}
    group_chat_counts = collections.defaultdict(int)

    if os.path.exists(LONG_TERM_DB_FILE):
        try:
            conn = sqlite3.connect(LONG_TERM_DB_FILE, timeout=5.0)
            cur = conn.cursor()
            # 扫描发言记录
            cur.execute("""
                SELECT sender_id, chat_id, sender_name 
                FROM chat_archive_fts 
                WHERE chat_id IS NOT NULL AND chat_id != ''
                ORDER BY rowid DESC LIMIT 8000
            """)
            for s_id, c_id, s_name in cur.fetchall():
                s_id_str = str(s_id).strip()
                c_id_str = str(c_id).strip()

                if c_id_str.startswith("private_"):
                    private_chat_users.add(s_id_str)
                elif c_id_str.startswith("group_") or c_id_str.isdigit():
                    clean_gid = c_id_str.replace("group_", "")
                    user_groups_map[s_id_str].add(clean_gid)
                    group_chat_counts[clean_gid] += 1

                if s_name and s_name.strip() and s_id_str not in user_nicknames_map:
                    user_nicknames_map[s_id_str] = clean_nickname(s_name.strip(), s_id_str)
            conn.close()
        except Exception:
            pass

    # 读取 allowed_groups
    allowed_groups = []
    if os.path.exists(ALLOWED_GROUPS_FILE):
        try:
            with open(ALLOWED_GROUPS_FILE, "r", encoding="utf-8") as f:
                ag = json.load(f)
                if isinstance(ag, list):
                    allowed_groups = [str(g).strip() for g in ag if str(g).strip()]
        except Exception:
            pass

    # 汇总所有真实的群聊 ID (使用真实群号，拒绝虚构别名)
    all_gids = [g[0] for g in sorted(group_chat_counts.items(), key=lambda x: x[1], reverse=True)]
    for g in allowed_groups:
        if g not in all_gids:
            all_gids.append(g)
    if not all_gids:
        all_gids = ["YOUR_GROUP_ID"]

    # 2. 核心天体：星见弥 (居中锚点，不限制四周自然浮动)
    nodes.append({
        "id": "node_bot",
        "name": "弥酱",
        "category": 0,
        "symbolSize": 60,
        "x": 0,
        "y": 0,
        "fixed": True,
        "value": "核心智能体 · 情感引力中枢",
        "itemStyle": {
            "color": "#ec4899",
            "shadowBlur": 26,
            "shadowColor": "rgba(236, 72, 153, 0.95)"
        },
        "label": {
            "show": True,
            "position": "inside",
            "fontSize": 13,
            "color": "#fff",
            "fontWeight": "bold"
        }
    })

    # 3. 主人专属节点 (专属金线直连弥酱)
    master_node_id = f"user_{master_str}"
    nodes.append({
        "id": master_node_id,
        "name": "👑 主人",
        "category": 1,
        "symbolSize": 50,
        "value": f"【最高权限 · 专属羁绊】QQ: {master_str}",
        "itemStyle": {
            "color": "#fbbf24",
            "shadowBlur": 22,
            "shadowColor": "rgba(251, 191, 36, 0.85)"
        },
        "label": { "show": True, "position": "top", "fontSize": 12, "color": "#fef08a", "fontWeight": "bold" }
    })
    links.append({
        "source": "node_bot",
        "target": master_node_id,
        "lineStyle": { "width": 4.5, "color": "#fbbf24", "curveness": -0.06 }
    })

    # 4. 二级群聊枢纽节点 (直接使用真实群号，如: 群 YOUR_GROUP_ID)
    group_node_ids = set()
    for idx, gid in enumerate(all_gids):
        g_node_id = f"group_{gid}"
        group_node_ids.add(g_node_id)
        g_name = f"群 {gid}"

        nodes.append({
            "id": g_node_id,
            "name": g_name,
            "category": 2,
            "symbolSize": 46,
            "value": f"【QQ群聊】群号: {gid} (发言数: {group_chat_counts.get(gid, 0)})",
            "itemStyle": {
                "color": "#6366f1",
                "shadowBlur": 20,
                "shadowColor": "rgba(99, 102, 241, 0.8)"
            },
            "label": {
                "show": True,
                "position": "bottom",
                "fontSize": 11,
                "color": "#e0e7ff",
                "fontWeight": "bold"
            }
        })

        # 弥酱 ⇄ 群枢纽主干连线
        links.append({
            "source": "node_bot",
            "target": g_node_id,
            "lineStyle": {
                "width": 3.2,
                "color": "#818cf8",
                "curveness": 0.08 * (1 if idx % 2 == 0 else -1)
            }
        })

    # 5. 加载用户画像并大幅扩容节点规模 (纳入所有有互动/好感或私聊的群友，规模达 80~120 人)
    profiles = {}
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    profiles = data
        except Exception:
            profiles = {}

    user_node_ids = {master_node_id}
    candidate_users = []

    for uid, prof in profiles.items():
        uid_str = str(uid)
        if uid_str == master_str or uid_str == str(BOT_QQ):
            continue

        aff = float(prof.get("affection", 0.0))
        icount = int(prof.get("interaction_count", 0))
        has_private = uid_str in private_chat_users

        # 只要有互动、有好感、或者私聊过，全部纳入展示！
        if icount > 0 or aff > 0 or has_private or (uid_str in user_groups_map):
            raw_nick = user_nicknames_map.get(uid_str, "")
            if not raw_nick:
                p_nick = prof.get("nickname", "")
                raw_nick = clean_nickname(p_nick, uid_str)

            candidate_users.append({
                "uid": uid_str,
                "name": raw_nick,
                "aff": aff,
                "icount": icount,
                "has_private": has_private,
                "prof": prof
            })

    # 按好感与互动量综合排序，扩容上限放宽至 100 人，保持网络宏大
    candidate_users.sort(key=lambda x: (1 if x["has_private"] else 0, x["aff"], x["icount"]), reverse=True)
    selected_users = candidate_users[:100]

    primary_gid = all_gids[0]

    for idx, u in enumerate(selected_users):
        uid_str = u["uid"]
        nick = u["name"]
        aff = u["aff"]
        icount = u["icount"]
        has_private = u["has_private"]
        node_id = f"user_{uid_str}"
        user_node_ids.add(node_id)

        # 类别划分
        if aff >= 850.0:
            cat_idx = 4  # 💖 挚友
            color = "#f472b6"
            size = 30
        elif aff >= 500.0:
            cat_idx = 5  # 🤝 朋友
            color = "#38bdf8"
            size = 24
        elif has_private:
            cat_idx = 3  # 💬 私聊常客
            color = "#f43f5e"
            size = 22
        else:
            cat_idx = 6  # ✨ 活跃群友
            color = "#10b981"
            size = 18

        nodes.append({
            "id": node_id,
            "name": nick,  # 纯净昵称，绝无冗长QQ
            "category": cat_idx,
            "symbolSize": size,
            "value": f"昵称: {nick} · QQ: {uid_str} · 好感度: {round(aff, 1)} · 互动: {icount}次 {'(已直通私聊)' if has_private else ''}",
            "itemStyle": { "color": color },
            "label": {
                "show": (cat_idx <= 5 or idx < 20),
                "position": "right",
                "fontSize": 10,
                "color": "#cbd5e1"
            },
            "emphasis": {
                "label": { "show": True, "fontSize": 12, "color": "#fff", "fontWeight": "bold" }
            }
        })

        # 【核心特性 1】：有私聊的直接连线到弥酱身上！
        if has_private:
            links.append({
                "source": "node_bot",
                "target": node_id,
                "lineStyle": {
                    "width": 2.2,
                    "color": "#f43f5e",
                    "curveness": 0.15,
                    "opacity": 0.75
                }
            })

        # 【核心特性 2】：群成员连线至对应的群 (支持跨多个群连线)
        in_groups = user_groups_map.get(uid_str, set())
        matched_gids = [g for g in all_gids if g in in_groups]
        if not matched_gids and not has_private:
            matched_gids = [primary_gid]

        for mgid in matched_gids:
            links.append({
                "source": f"group_{mgid}",
                "target": node_id,
                "lineStyle": {
                    "width": 1.2,
                    "color": color,
                    "opacity": 0.45
                }
            })

    # 6. 读取 L3 永久记忆事实
    if os.path.exists(LONG_TERM_DB_FILE):
        try:
            conn = sqlite3.connect(LONG_TERM_DB_FILE, timeout=3.0)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT id, target_id, category, content, status 
                FROM permanent_facts 
                WHERE status = 'active'
                ORDER BY timestamp DESC LIMIT 30
            """)
            facts = cur.fetchall()
            conn.close()

            for f_idx, f in enumerate(facts):
                fid = f["id"]
                target = str(f["target_id"])
                cat = f["category"]
                content = str(f["content"])

                is_group_fact = cat in ["group_meme", "milestone"] or target in all_gids
                cat_idx = 8 if is_group_fact else 7
                color = "#c084fc" if is_group_fact else "#38bdf8"
                short_label = content[:10] + ("..." if len(content) > 10 else "")
                fact_node_id = f"fact_{fid}"

                target_user_node = f"user_{target}"
                if is_group_fact:
                    parent_node = f"group_{primary_gid}"
                elif target_user_node in user_node_ids:
                    parent_node = target_user_node
                else:
                    parent_node = "node_bot"

                nodes.append({
                    "id": fact_node_id,
                    "name": short_label,
                    "category": cat_idx,
                    "symbolSize": 14,
                    "value": f"[{cat}] {content}",
                    "itemStyle": { "color": color, "opacity": 0.85 },
                    "label": { "show": False },
                    "emphasis": {
                        "label": { "show": True, "fontSize": 10, "color": "#fff" }
                    }
                })

                links.append({
                    "source": parent_node,
                    "target": fact_node_id,
                    "lineStyle": {
                        "width": 1.0,
                        "color": color,
                        "type": "dotted",
                        "opacity": 0.5
                    }
                })
        except Exception:
            pass

    return {
        "success": True,
        "categories": categories,
        "nodes": nodes,
        "links": links
    }
