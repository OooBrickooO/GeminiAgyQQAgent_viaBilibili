"""
toys/games — 弥酱桌面小游戏模块化引擎子包

拆分结构：
- session_store.py: 会话持久化、feiyu-mood 棋风调制、局末好感/心情/L2 结算、战术事件台词翻译
- gomoku_engine.py: 猫爪五子棋 (15x15) 棋型评估、VCF 算杀、Alpha-Beta 搜索与步进逻辑
- othello_engine.py: 翻转黑白棋 (8x8) 位置+行动力+终局搜索、自动 Pass 与步进逻辑
- cards_and_numbers.py: 猫猫21点 (含每日御神签联动) 与 1A2B 破译密码引擎
"""
