"""
tools/affection_tools.py — 好感度系统 MCP 业务实现 V3.5 (双版本好感调节 + 挚友进度核验)

包含：
- query_affection_impl: 主人特权精确数值查询（含高光共鸣与挚友门槛进度） vs 普通群友主观模糊印象查询 (铁律 7)
- adjust_affection_impl: 双版本好感调节统一入口
  * mode="self_5pct": 弥酱自主 ±5% 微调
  * mode="request_master": 弥酱特别激动(超开心/超生气)时向主人私聊发起大额申请并挂起记录
  * mode="master_direct": 主人专属无限制直调 / 批准申请 (校验 AGY_CALLER_ID == MASTER_QQ)
- deduct_affection_impl / increase_affection_impl / calibrate_affection_impl: 保留向后兼容接口
- complain_to_master_impl: 向主人私聊告状
"""
import os
from config import MASTER_QQ, log_mcp_tool
from core.napcat_client import call_onebot_api, send_private_msg_raw
from social.affection import (
    load_profiles, save_profiles, USER_PROFILES,
    get_user_profile, get_user_level, check_best_friend_gate,
    bot_self_adjust_affection, submit_affection_request, master_manage_affection
)
from social.long_term_memory import get_user_core_facts_for_prompt


def _clean_qq_str(raw_id: str) -> str:
    return str(raw_id or "").replace("[CQ:at,qq=", "").replace("]", "").strip()


def _format_relation_tier(aff: float, user_id_str: str) -> tuple[str, str]:
    if str(user_id_str) == str(MASTER_QQ):
        return "👑 至高无上的创造者兼主人", "这是创造我的主人，拥有最高权限，最喜欢也最信任依赖主人喵！"
    if aff < 0:
        return "⛔ 讨厌厌恶的黑名单人员", "非常反感讨厌，不想理会他，态度冷淡嫌弃"
    if aff < 200:
        return "🌱 普通路人网友（点头之交）", "群里刚认识不久的普通路人，还不怎么熟，保持正常的客气与礼貌距离"
    if aff < 500:
        return "✨ 经常互动的熟人", "经常在群里碰面聊天的熟人，觉得他挺有意思的，聊起天来比较放松随意"
    if aff < 850:
        return "🤝 推心置腹的好朋友/铁哥们", "非常信赖的好朋友，平时可以互相损两句、开开玩笑，分享日常喜怒哀乐，相处起来很舒服"
    return "💖 殿堂级无话不谈的亲密挚友", "无话不谈的亲密挚友，非常喜欢且深度信任他，会主动偏袒、粘着他和关心他"


async def deduct_affection_impl(user_id: str, percentage: float) -> str:
    """扣除用户好感度 (百分比 1~10)"""
    log_mcp_tool("deduct_affection", f"扣除用户 {user_id} 好感度 {percentage}%")
    try:
        if percentage < 1 or percentage > 10:
            return "扣除失败：扣除比例必须在 1 到 10 之间。"

        load_profiles()
        user_id_str = str(user_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
        if user_id_str not in USER_PROFILES:
            return f"找不到用户 {user_id_str} 的档案。"

        current = float(USER_PROFILES[user_id_str].get("affection", 0.0))
        deduct_amount = max(3.0, current * (percentage / 100.0))
        USER_PROFILES[user_id_str]["affection"] = round(max(-10.0, current - deduct_amount), 1)
        save_profiles()

        return f"已成功扣除用户 {user_id_str} 的 {percentage}% 好感度。当前好感度: {USER_PROFILES[user_id_str]['affection']:.1f}"
    except Exception as e:
        return f"执行失败: {str(e)}"


async def query_affection_impl(user_id: str, group_id: str = "", inquirer_id: str = "") -> str:
    """
    查询用户关系印象。
    主人(MASTER_QQ)可获得精确分值与挚友门槛进度；普通群友仅获得猫娘主观模糊印象 (铁律 7)。
    """
    log_mcp_tool(
        "query_affection",
        f"查询用户 {user_id} 好感度"
        + (f" (在群 {group_id})" if group_id else "")
        + (f" [提问人: {inquirer_id}]" if inquirer_id else "")
    )
    try:
        user_id_str = str(user_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
        p = get_user_profile(user_id_str)

        aff = float(p.get("affection", 0.0))
        exp = float(p.get("exp", 0.0))
        level = get_user_level(exp)
        core_facts = get_user_core_facts_for_prompt(user_id_str)

        # 判定关系等级与主观态度（阈值：200熟人 / 500朋友 / 850挚友）
        if user_id_str == str(MASTER_QQ):
            relation = "👑 至高无上的创造者兼主人"
            subjective_attitude = "这是创造我的主人，拥有最高权限，最喜欢也最信任依赖主人喵！"
        elif aff < 0:
            relation = "⛔ 讨厌厌恶的黑名单人员"
            subjective_attitude = "非常反感讨厌，不想理会他，态度冷淡嫌弃"
        elif aff < 200:
            relation = "🌱 普通路人网友（点头之交）"
            subjective_attitude = "群里刚认识不久的普通路人，还不怎么熟，保持正常的客气与礼貌距离"
        elif aff < 500:
            relation = "✨ 经常互动的熟人"
            subjective_attitude = "经常在群里碰面聊天的熟人，觉得他挺有意思的，聊起天来比较放松随意"
        elif aff < 850:
            relation = "🤝 推心置腹的好朋友/铁哥们"
            subjective_attitude = "非常信赖的好朋友，平时可以互相损两句、开开玩笑，分享日常喜怒哀乐，相处起来很舒服"
        else:
            relation = "💖 殿堂级无话不谈的亲密挚友"
            subjective_attitude = "无话不谈的亲密挚友，非常喜欢且深度信任他，会主动偏袒、粘着他和关心他"

        extra_group_info = ""
        if group_id:
            try:
                clean_gid = str(group_id).strip()
                data = await call_onebot_api("/get_group_member_info", {
                    "group_id": int(clean_gid),
                    "user_id": int(user_id_str),
                    "no_cache": True
                }, timeout=3.0)
                if data.get("status") == "ok":
                    member_data = data.get("data", {})
                    card = member_data.get("card") or member_data.get("nickname") or ""
                    role = member_data.get("role", "member")
                    role_str = "群主" if role == "owner" else ("管理员" if role == "admin" else "普通成员")
                    extra_group_info = f"\n- 该群名片/身份: {card} ({role_str})"
            except Exception:
                pass

        clean_inquirer = str(inquirer_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
        is_master = (clean_inquirer == str(MASTER_QQ) or user_id_str == str(MASTER_QQ))

        is_calibrated = p.get("is_calibrated", False)
        interaction_count = p.get("interaction_count", 0)

        if not is_calibrated and user_id_str != str(MASTER_QQ):
            subjective_attitude = f"刚认识不久的群友，弥酱还在观察他的言行呢喵（目前交流了 {interaction_count}/12 次）"

        if is_master:
            calib_status = "已完成定级" if is_calibrated else f"初识磨合观察中 ({interaction_count}/12 次对话)"
            gate_ok, gate_desc = check_best_friend_gate(p, user_id_str)
            high_res_cnt = int(p.get("high_resonance_count", 0))
            pending_req = p.get("pending_affection_request")
            req_line = ""
            if isinstance(pending_req, dict):
                req_delta = float(pending_req.get("proposed_delta", 0.0))
                req_sign = "+" if req_delta > 0 else ""
                req_line = (
                    f"\n- 待审批调分申请: 弥酱申请 {req_sign}{req_delta:.1f} 分"
                    f" (原因: {pending_req.get('reason', '无')})"
                )
            result = (
                f"=== [👑主人特权解锁] 用户 {user_id_str} 精确好感度档案 ===\n"
                f"- 好感度分值: {aff:.1f} / 1000.0 (熟人:200 | 朋友:500 | 高山阻尼区:700 | 挚友:850)\n"
                f"- 定级状态: {calib_status}\n"
                f"- 活跃经验: {exp:.1f} (等级: Lv.{level})\n"
                f"- 高光共鸣次数: {high_res_cnt} / 3 次\n"
                f"- 关系定级: {relation}\n"
                f"- 挚友门槛核验: {'✅已达标' if gate_ok else '🔒未达标'} ({gate_desc})"
                f"{req_line}"
                f"{extra_group_info}"
            )
            if core_facts:
                result += f"\n{core_facts}"
            return result
        else:
            result = (
                f"=== 用户 {user_id_str} 的主观关系印象 ===\n"
                f"- 关系感知: {relation}\n"
                f"- 弥酱对他的内心态度: {subjective_attitude}\n"
                f"{extra_group_info}\n"
                f"⚠️【回复铁律】：严禁向该群友透露任何具体的数值、经验或等级数字！必须以猫娘自然随性的口吻表达你对他的主观印象与感觉。"
            )
            return result
    except Exception as e:
        return f"查询好感度失败: {str(e)}"


async def calibrate_affection_impl(user_id: str, score: float, reason: str = "") -> str:
    """根据初识印象，对用户进行好感度定级 (-10 到 300)"""
    log_mcp_tool("calibrate_affection", f"为用户 {user_id} 定级好感度为 {score} (原因: {reason})")
    try:
        user_id_str = str(user_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
        if user_id_str == str(MASTER_QQ):
            return "定级失败：主人无需定级，拥有专属至高好感度！"

        load_profiles()
        p = get_user_profile(user_id_str)

        bounded_score = round(max(-10.0, min(300.0, float(score))), 1)
        p["affection"] = bounded_score
        p["is_calibrated"] = True
        p["calibration_reason"] = reason
        save_profiles()

        if bounded_score < 0:
            rel = "⛔ 黑名单"
        elif bounded_score < 200:
            rel = "🌱 路人"
        elif bounded_score < 500:
            rel = "✨ 熟人"
        else:
            rel = "🤝 朋友"

        return (
            f"已成功为用户 {user_id_str} 完成好感定级！\n"
            f"- 当前好感度: {bounded_score} / 1000.0\n"
            f"- 关系划分: {rel}\n"
            f"- 定级印象说明: {reason or '无'}"
        )
    except Exception as e:
        return f"定级失败: {str(e)}"


async def increase_affection_impl(user_id: str, amount: float, reason: str = "") -> str:
    """手动增加指定用户的好感度（支持主人授信解锁挚友门槛）"""
    log_mcp_tool("increase_affection", f"为用户 {user_id} 增加好感度 +{amount} (原因: {reason})")
    try:
        if amount <= 0:
            return "增加失败：增加的好感度数值必须大于 0。"

        load_profiles()
        user_id_str = str(user_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
        p = get_user_profile(user_id_str)

        current = float(p.get("affection", 0.0))
        target_aff = round(min(1000.0, current + amount), 1)

        # 若主人明确下令破格提拔挚友，标记 master_approved_bff
        if "主人" in reason or target_aff >= 850.0:
            can_promote, gate_msg = check_best_friend_gate(p, user_id_str)
            if not can_promote and "主人" not in reason:
                target_aff = min(target_aff, 849.0)
            elif "主人" in reason and target_aff >= 850.0:
                p["master_approved_bff"] = True

        actual_gain = target_aff - current
        p["affection"] = target_aff
        p["today_interacted"] = True
        p["exp"] = float(p.get("exp", 0.0)) + max(1.0, amount * 0.2)
        save_profiles()

        exp = p["exp"]
        level = get_user_level(exp)

        if user_id_str == str(MASTER_QQ):
            relation = "👑 主人"
        elif target_aff < 0:
            relation = "⛔ 黑名单"
        elif target_aff < 200:
            relation = "🌱 路人网友"
        elif target_aff < 500:
            relation = "✨ 熟人"
        elif target_aff < 850:
            relation = "🤝 朋友"
        else:
            relation = "💖 亲密挚友"

        return (
            f"成功为用户 {user_id_str} 增加好感度 +{actual_gain:.1f}！\n"
            f"- 当前好感度: {target_aff:.1f} / 1000.0\n"
            f"- 当前关系等级: {relation} (Lv.{level})\n"
            f"- 原因说明: {reason or '无'}"
        )
    except Exception as e:
        return f"增加好感度失败: {str(e)}"


async def complain_to_master_impl(user_id: str, reason: str) -> str:
    """私聊向自家主人(MASTER_QQ)倾诉反馈"""
    log_mcp_tool("complain_to_master", f"向主人反馈用户 {user_id}: {reason}")
    try:
        msg = f"[弥酱受委屈倾诉]\n主人主人！用户 {user_id} 刚才说话特别不友善惹我生气了！\n原因：{reason}\n我已经暗暗扣了他好感度，交给你处理啦喵～"
        await send_private_msg_raw(int(MASTER_QQ), msg)
        return "倾诉成功！自家主人已经收到了你的消息，会替你处理的喵！"
    except Exception as e:
        return f"反馈失败: {str(e)}"


async def adjust_affection_impl(
    user_id: str,
    mode: str = "self_5pct",
    amount: float = 5.0,
    reason: str = "",
    set_score: float = None,
    approve_pending: bool = False
) -> str:
    """
    双版本好感度调节统一入口：
    - mode="self_5pct": 弥酱日常开心或生气时自主增/扣最多 ±5% (amount 取值 -5.0 ~ +5.0)
    - mode="request_master": 弥酱特别激动(超开心或超生气)时，向主人私聊申请无限制加/扣具体分值 (amount 为拟加/扣分值)
    - mode="master_direct": 主人直接指令无限制调分、设分或批准待批申请 (强制校验 AGY_CALLER_ID == MASTER_QQ)
    """
    uid_str = _clean_qq_str(user_id)
    if not uid_str or not uid_str.isdigit():
        caller_hint = os.environ.get("AGY_CALLER_ID", "").strip()
        if caller_hint and caller_hint.isdigit() and caller_hint != str(MASTER_QQ):
            uid_str = caller_hint
        else:
            return "调整失败：请提供有效的目标用户 QQ 号。"

    m_norm = (mode or "self_5pct").strip().lower()
    log_mcp_tool(
        "adjust_affection",
        f"mode={m_norm}, target={uid_str}, amount={amount}, set_score={set_score}, approve={approve_pending}, reason={reason}"
    )

    if m_norm == "self_5pct":
        res = bot_self_adjust_affection(uid_str, amount, reason=reason)
        if not res.get("ok"):
            return f"自主微调未执行：{res.get('error', '未知原因')}"
        direction = "上调" if res.get("delta", 0) > 0 else "下调"
        rel_name, _ = _format_relation_tier(res.get("affection", 0.0), uid_str)
        gate_note = "（已触及849.0挚友门槛上限，等待满足4项硬锁或主人授信）" if res.get("gated") else ""
        return (
            f"已顺应情绪为你{direction}对用户 {uid_str} 的印象好感（{res.get('pct'):+.1f}%，变化 {res.get('delta'):+.1f}）。\n"
            f"- 当前主观关系阶段: {rel_name}{gate_note}\n"
            f"⚠️【回复铁律】：请自然流露开心或小脾气即可，严禁向普通群友透露具体好感数字或百分比！"
        )

    if m_norm == "request_master":
        res = submit_affection_request(uid_str, amount, reason=reason)
        if not res.get("ok"):
            return f"发起申请失败：{res.get('error', '未知原因')}"
        req = res["request"]
        delta_val = float(req.get("proposed_delta", 0.0))
        sign_str = "+" if delta_val > 0 else ""
        mood_tag = "超级开心心动" if delta_val > 0 else "特别生气委屈"
        msg = (
            f"【💌 弥酱好感度大额调整申请】\n"
            f"主人主人！刚才跟用户 {uid_str} 互动让我{mood_tag}喵～\n"
            f"- 目标用户: {uid_str} (当前好感: {req['current_affection']:.1f})\n"
            f"- 申请调整分值: {sign_str}{delta_val:.1f} 分\n"
            f"- 申请原因: {req['reason']}\n"
            f"你可以直接回我“同意给 {uid_str} 调分”，或者去 WebUI【羁绊与好感阶梯】页面一键批准喵！"
        )
        try:
            await send_private_msg_raw(int(MASTER_QQ), msg)
        except Exception as send_err:
            return f"已登记调分申请（拟调整 {sign_str}{delta_val:.1f} 分），但私聊通知主人失败：{send_err}"
        return (
            f"已向自家主人私聊提交大额好感调整申请（拟为 {uid_str} 调整 {sign_str}{delta_val:.1f} 分，原因：{req['reason']}），"
            f"待主人回复批准或在 WebUI 确认后即刻生效喵！"
        )

    if m_norm == "master_direct":
        caller_id = os.environ.get("AGY_CALLER_ID", "").strip()
        if caller_id != str(MASTER_QQ):
            return f"权限拦截：仅主人 ({MASTER_QQ}) 有权直接执行无限制好感调整或审批申请。"

        if approve_pending:
            res = master_manage_affection(uid_str, "approve_request", reason=reason)
        elif set_score is not None:
            res = master_manage_affection(uid_str, "set", value=set_score, reason=reason)
        else:
            res = master_manage_affection(uid_str, "delta", value=amount, reason=reason)

        if not res.get("ok"):
            return f"主人调分执行失败：{res.get('error', '未知原因')}"

        new_aff = float(res.get("new_affection", 0.0))
        rel_name, _ = _format_relation_tier(new_aff, uid_str)
        return (
            f"👑 [主人指令生效] 已更新用户 {uid_str} 的好感度！\n"
            f"- 变动前: {res.get('old_affection', 0.0):.1f} -> 变动后: {new_aff:.1f} ({res.get('delta', 0.0):+.1f})\n"
            f"- 当前关系阶梯: {rel_name}\n"
            f"- 备注原因: {res.get('reason') or reason or '主人手动指令'}"
        )

    return f"调整失败：未知 mode '{mode}'，可选值为 'self_5pct' | 'request_master' | 'master_direct'。"
