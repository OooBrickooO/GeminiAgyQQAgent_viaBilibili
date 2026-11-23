/**
 * DeskPet/src/host-daemon.ts — 星见弥 (弥酱) 桌宠本地感官与 CUA 守护宿主
 *
 * 100% 继承 Coopanion 反作弊安全架构 (Node.js + Electron + koffi 无钩子轮询 + GDI BitBlt 子进程)：
 * 1. 本地服务 (7797)：驱动 Electron 透明穿透桌宠窗口 (/pet, /dress, /socket) 与星见弥形象包 (web/mi/)；
 * 2. CUA 引擎子进程 (src/cua/engine-child.ts)：多屏截屏、局部高清 Zoom、窗口枚举与焦点切换、键鼠注入、2秒人类静止让位锁、桌宠气泡四档权限确认；
 * 3. 伴随式主动看屏探针 (Proactive Screen Watcher)：支持游戏局内实时战况吐槽、场景切换吐槽、长时专注与深夜陪伴，配备三端联动总开关 (proactivePeek)；
 * 4. 无钩子全局热键与语音输入 (src/asr/hotkey.ts)：30ms GetAsyncKeyState 轮询 LeftAlt*2 按住说话 (Windows SAPI 实时听写) 与 LeftCtrl*2 双击打字；
 * 5. 9D 真声播放桥：支持接收或直接向本机 :9880 GPT-SoVITS-v5 请求 WAV 语音，驱动桌宠立体声与音画同步律动；
 * 6. 跨机/本机双向通信：接收边缘服/本机 qqAgent 的 /api/mcp/* 调用，并将桌宠事件 ([typed]/[voice]/[answer]/[touch]/[screen_peek]) 投递至 bot_daemon (:18302/deskpet_event)。
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { fork, spawn, type ChildProcess } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { createWriteStream, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { dirname, extname, join, normalize, sep } from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath } from 'node:url';
import { WebSocketServer, type WebSocket } from 'ws';

import { parseActions, parseScript, estimateSeconds, type VocabWord } from './script.ts';
import { parseHotkey, watchHotkey, hotkeyBadge, hotkeyLabel, TAP_MAX_MS, type KeyWatcher } from './asr/hotkey.ts';
import { Segmenter } from './asr/segmenter.ts';
import { SENSEVOICE, SherpaAsr } from './asr/sherpa.ts';
import { SystemRecognizer, systemRecognizerSupported, type Logger, type SystemSentence } from './asr/system-recognizer.ts';
import { isTerminal, opensRunBox } from './cua/terminal.ts';
import { fit } from './cua/engine/image.ts';
import { parseKeys } from './cua/engine/keys.ts';
import { MODEL_TEXT } from './cua/model-text.ts';
import type {
  Button, ChildToMain, EngineRequest, HoldResult, InputResult,
  MainToChild, ScreenInfo, ScreenshotResult, Target, TypeResult, WindowEntry, ZoomResult
} from './cua/engine-ipc.ts';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = normalize(join(__dirname, '..'));
const WEB_DIR = join(ROOT_DIR, 'web');
const HOST_MAIN_CJS = join(ROOT_DIR, 'host', 'electron-main.cjs');
const ENGINE_CHILD_TS = join(__dirname, 'cua', 'engine-child.ts');
const CONFIG_FILE = join(ROOT_DIR, 'config.json');
const SENSEVOICE_DIR = join(ROOT_DIR, 'models', 'sensevoice-small-int8');
const SENSEVOICE_FILES = [
  {
    name: 'tokens.txt',
    minBytes: 100_000,
    urls: [
      'https://modelscope.cn/models/pengzhendong/sherpa-onnx-sense-voice-zh-en-ja-ko-yue/resolve/master/tokens.txt',
      'https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/tokens.txt',
    ],
  },
  {
    name: 'model.int8.onnx',
    minBytes: 200_000_000,
    urls: [
      'https://modelscope.cn/models/pengzhendong/sherpa-onnx-sense-voice-zh-en-ja-ko-yue/resolve/master/model.int8.onnx',
      'https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx',
    ],
  },
];

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.json': 'application/json; charset=utf-8',
  '.ogg': 'audio/ogg',
  '.mp3': 'audio/mpeg',
  '.wav': 'audio/wav',
};

const PAGES: Record<string, string> = { '/pet': 'pet.html', '/dress': 'dress.html' };
const PACK_CSP = "sandbox allow-scripts; default-src 'none'";
const PAGE_CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; img-src 'self' data: blob:; media-src 'self' data: blob:; worker-src 'self' blob:; frame-ancestors 'self' http://127.0.0.1:* http://localhost:*";

// 常见游戏进程与标题特征（用于识别游戏局内吐槽契机）
const GAME_PROCESS_RE = /(genshin|yuanshen|starrail|zenless|wuthering|client-win64-shipping|blackmyth|b1-win64|eldenring|cyberpunk|cs2|dota2|league of legends|valorant|overwatch|apex|helldivers|palworld|minecraft|javaw|terraria|stardew|hollow_knight|sekiro|monsterhunter|naraka|pubg|tslgame|deltaforce|steamapps)/i;
const GAME_TITLE_RE = /(原神|崩坏|星穹铁道|绝区零|鸣潮|黑神话|悟空|艾尔登法环|赛博朋克|无畏契约|英雄联盟|守望先锋|绝地求生|永劫无间|三角洲行动|明日方舟|碧蓝航线|尘白禁区|Steam)/i;
const CODING_PROCESS_RE = /(code|devenv|pycharm|idea64|webstorm|clion|goland|cursor|windsurf|antigravity|sublime_text|nvim|vim)/i;

function createLogger(prefix = 'DeskPet'): Logger {
  return {
    info: (m) => console.log(`🐾 [${prefix}] ${m}`),
    warn: (m) => console.warn(`⚠️ [${prefix}] ${m}`),
    error: (m) => console.error(`❌ [${prefix}] ${m}`),
    debug: () => {},
    child: (sub) => createLogger(`${prefix}:${sub}`),
  };
}
const log = createLogger();

// 读取与保存配置
function loadConfig() {
  try {
    if (existsSync(CONFIG_FILE)) {
      return JSON.parse(readFileSync(CONFIG_FILE, 'utf8'));
    }
  } catch (e) {
    log.warn(`读取 config.json 失败，使用默认配置: ${(e as Error).message}`);
  }
  return {};
}

const cfg = loadConfig();
const PORT: number = Number(cfg.port) || 7797;
const AUTH_TOKEN: string = String(cfg.token || process.env.DESKPET_TOKEN || 'YOUR_NAPCAT_TOKEN');
const DEFAULT_BRAIN_CANDIDATES = [
  'http://127.0.0.1:18302',
  'http://127.0.0.1:8088',
  'http://YOUR_EDGE_NODE_IP:8088',
  'http://YOUR_EDGE_NODE_IP:18302',
];
const BRAIN_CANDIDATES: string[] = Array.from(new Set([
  ...(Array.isArray(cfg.brainCandidates) ? cfg.brainCandidates : []),
  ...DEFAULT_BRAIN_CANDIDATES,
]));

function saveConfig() {
  try {
    writeFileSync(CONFIG_FILE, JSON.stringify(cfg, null, 2), 'utf8');
  } catch (e) {
    log.warn(`保存 config.json 失败: ${(e as Error).message}`);
  }
}

// 加载内置形象包 (mi / coo / whale)
interface LoadedPack {
  id: string;
  base: string;
  dir: string;
  builtin: boolean;
  manifest: Record<string, any>;
  vocab: VocabWord[];
}

function loadBuiltinPacks(): LoadedPack[] {
  const out: LoadedPack[] = [];
  for (const id of ['mi', 'coo', 'whale']) {
    const dir = join(WEB_DIR, id);
    const mfPath = join(dir, 'figure.json');
    if (!existsSync(mfPath)) continue;
    try {
      const m = JSON.parse(readFileSync(mfPath, 'utf8'));
      out.push({
        id: m.id || id,
        base: `/web/${id}/`,
        dir,
        builtin: true,
        manifest: m,
        vocab: Array.isArray(m.vocab) ? m.vocab : [],
      });
    } catch (e) {
      log.warn(`加载形象包 ${id} 失败: ${(e as Error).message}`);
    }
  }
  return out;
}

const PACKS = loadBuiltinPacks();
function currentPack(): LoadedPack {
  const figId = cfg.skin?.figure || 'mi';
  return PACKS.find((p) => p.id === figId) || PACKS[0]!;
}

// =========================================================================
// 1. 内存 WAV 语音缓存池 (支撑 GPT-SoVITS-v5 9D 真声与气泡打字机同步)
// =========================================================================
const ttsAudioCache = new Map<string, { buf: Buffer; createdAt: number; duration: number }>();

function calcWavDuration(buf: Buffer): number {
  try {
    if (buf.length > 44 && buf.toString('ascii', 0, 4) === 'RIFF') {
      const byteRate = buf.readUInt32LE(28);
      if (byteRate > 0) {
        return Math.round(((buf.length - 44) / byteRate) * 100) / 100;
      }
    }
  } catch {}
  return Math.max(1.2, Math.round((buf.length / 64000) * 10) / 10);
}

function storeTtsWav(buf: Buffer): { id: string; url: string; duration: number } {
  const now = Date.now();
  for (const [k, v] of ttsAudioCache.entries()) {
    if (now - v.createdAt > 5 * 60_000) ttsAudioCache.delete(k);
  }
  const id = randomUUID();
  const duration = calcWavDuration(buf);
  ttsAudioCache.set(id, { buf, createdAt: now, duration });
  return { id, url: `/api/tts/${id}.wav`, duration };
}

function toBuffer(raw: any): Buffer {
  if (!raw) return Buffer.alloc(0);
  if (Buffer.isBuffer(raw)) return raw;
  if (raw instanceof Uint8Array) return Buffer.from(raw);
  if (Array.isArray(raw?.data)) return Buffer.from(raw.data);
  if (typeof raw === 'object') return Buffer.from(Object.values(raw) as number[]);
  return Buffer.alloc(0);
}

// =========================================================================
// 2. Win32 CUA 引擎子进程管理器 (100% 复用 Coopanion src/cua/engine-child.ts)
// =========================================================================
class CuaEngineManager {
  private child: ChildProcess | null = null;
  private seq = 0;
  private readonly pending = new Map<number, { done: (v: any) => void; fail: (e: Error) => void; timer: NodeJS.Timeout }>();
  public latestScreen: ScreenInfo | null = null;
  public latestView: (Target & { width: number; height: number; scale: number }) | null = null;
  public grantedUntil = 0;
  public terminalGrantedUntil = 0;

  private ensureChild(): ChildProcess {
    if (this.child && this.child.connected) return this.child;
    const child = fork(ENGINE_CHILD_TS, [], {
      stdio: ['ignore', 'inherit', 'inherit', 'ipc'],
      execArgv: ['--experimental-transform-types', '--no-warnings'],
      serialization: 'advanced',
    });
    child.on('message', (msg: ChildToMain) => {
      const entry = this.pending.get(msg.id);
      if (!entry) return;
      clearTimeout(entry.timer);
      this.pending.delete(msg.id);
      if (msg.ok) entry.done(msg.value);
      else {
        const fn = msg.code ? MODEL_TEXT.zh.engineErrors[msg.code] : undefined;
        const text = fn ? fn(...(msg.args ?? [])) : (msg.error || msg.code || 'engine error');
        entry.fail(new Error(text));
      }
    });
    child.on('exit', () => {
      if (this.child === child) this.child = null;
      for (const [, p] of this.pending) {
        clearTimeout(p.timer);
        p.fail(new Error('CUA engine process exited'));
      }
      this.pending.clear();
    });
    this.child = child;
    return child;
  }

  async request<T = any>(req: EngineRequest, timeoutMs = 25_000): Promise<T> {
    const child = this.ensureChild();
    const id = ++this.seq;
    return new Promise<T>((done, fail) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        try { child.kill(); } catch {}
        this.child = null;
        fail(new Error(`CUA engine request timeout (${req.op})`));
      }, timeoutMs);
      this.pending.set(id, { done, fail, timer });
      child.send({ id, req } satisfies MainToChild);
    });
  }

  /** 将最新截图上的像素坐标转换为显示器物理像素坐标 */
  toDisplayCoords(x: number, y: number): { target: Target; px: number; py: number } {
    if (!this.latestView) throw new Error('尚未截取屏幕，请先调用 pet_cua_screenshot 获取当前屏幕画面与坐标基准');
    const scale = this.latestView.scale || 1;
    return {
      target: { display: this.latestView.display, layout: this.latestView.layout },
      px: Math.round(x / scale),
      py: Math.round(y / scale),
    };
  }

  stop() {
    if (this.child) {
      try { this.child.kill(); } catch {}
      this.child = null;
    }
  }
}

const cuaEngine = new CuaEngineManager();

// =========================================================================
// 3. 向 qqAgent 大脑 (bot_daemon :18302) 投递桌宠事件
// =========================================================================
let activeBrainUrl = '';

async function pushEventToBrain(payload: Record<string, unknown>): Promise<boolean> {
  const candidates = activeBrainUrl
    ? [activeBrainUrl, ...BRAIN_CANDIDATES.filter((u) => u !== activeBrainUrl)]
    : BRAIN_CANDIDATES;

  let saw404Host = '';
  for (const base of candidates) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 4500);
      const res = await fetch(`${base}/deskpet_event`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Authorization': `Bearer ${AUTH_TOKEN}`,
        },
        body: JSON.stringify(payload),
        signal: ctrl.signal,
      });
      clearTimeout(timer);
      if (res.ok) {
        if (activeBrainUrl !== base) {
          activeBrainUrl = base;
          log.info(`已连通弥酱大脑事件网关: ${base}/deskpet_event`);
        }
        return true;
      }
      if (res.status === 404) {
        saw404Host = base;
      }
    } catch {
      // 尝试下一个候选地址
    }
  }
  if (saw404Host) {
    log.warn(`已连通远端弥酱服务器 (${saw404Host})，但远端返回 404（尚未热更新 V5.4 桌宠补丁）！请将 update_patch.zip 发送到弥酱 QQ 私聊完成热更新。`);
  } else {
    log.warn(`无法连接弥酱大脑 (:18302 或 :8088 /deskpet_event)，请确认 bot_daemon.py 是否已启动且已应用最新 update_patch.zip`);
  }
  return false;
}

// =========================================================================
// 4. 桌宠页面状态、触摸防抖聚合与问答回调管理
// =========================================================================
let petWs: WebSocket | null = null;
let petIsWindow = false;
const dressers = new Set<WebSocket>();
const windowKey = randomUUID();
let startX: number | undefined = typeof cfg.startX === 'number' ? cfg.startX : 0.78;
let startY: number | undefined = typeof cfg.startY === 'number' ? cfg.startY : undefined;
let currentStatus: Record<string, unknown> | null = null;

// 提问 / 权限确认气泡等待表
const pendingConfirms = new Map<string, { resolve: (idx: number | null) => void; timer: NodeJS.Timeout }>();

function sendToPet(msg: Record<string, unknown>): boolean {
  if (!petWs || petWs.readyState !== petWs.OPEN) return false;
  petWs.send(JSON.stringify(msg));
  return true;
}

function broadcastPrefs(patch: Record<string, unknown>) {
  const text = JSON.stringify({ t: 'prefs', ...patch });
  for (const ws of [petWs, ...dressers]) {
    if (ws && ws.readyState === ws.OPEN) ws.send(text);
  }
}

function getSnapshot() {
  const p = cfg.prefs || {};
  const s = cfg.skin || { figure: 'mi', scheme: 'starlight', palette: 'purple', head: 'none', side: 'none', glasses: 'none', neck: 'none' };
  const asrCfg = cfg.asr || {};
  return {
    skin: s,
    roam: p.roam ?? 'calm',
    sound: p.sound !== false,
    theme: p.theme ?? 'dark',
    scale: Number(p.scale) || 1,
    bubbleSize: Number(p.bubbleSize) || 1,
    fontSize: Number(p.fontSize) || 1,
    bubbleSpeed: Number(p.bubbleSpeed) || 1,
    sayBubbleHoldSec: Number(p.sayBubbleHoldSec) || 0,
    glow: Number(p.glow) ?? 7,
    user: p.user || '主人',
    hoverButtons: Array.isArray(p.hoverButtons) ? p.hoverButtons : ['chat', 'voice', 'peek', 'roam', 'size', 'dress'],
    rememberPosition: p.rememberPosition !== false,
    doubleClickChat: p.doubleClickChat !== false,
    statusBubble: p.statusBubble !== false,
    saidBubbles: p.saidBubbles !== false,
    saidBubbleHoldSec: Number(p.saidBubbleHoldSec) || 0,
    hideWhenFullscreen: !!p.hideWhenFullscreen,
    passWhenFullscreen: !!p.passWhenFullscreen,
    overTaskbar: p.overTaskbar !== false,
    hideFromCapture: !!p.hideFromCapture,
    clickThrough: !!p.clickThrough,
    proactivePeek: p.proactivePeek !== false,
    perchWindows: p.perchWindows !== false,
    micDevice: typeof p.micDevice === 'string' ? p.micDevice : '',
    micDevices: asrState.devices,
    activeMicLabel: asrState.activeMicLabel,
    // Keep microphone stream pre-warmed whenever voice input is enabled so pressing LeftAlt*2 has 0ms cold-start latency even when Oopz is closed!
    mic: asrCfg.enabled !== false,
    voice: {
      enabled: asrCfg.enabled !== false,
      ready: asrState.ready,
      detail: asrState.detail,
      key: hotkeyBadge(asrCfg.hotkey || 'LeftAlt*2'),
      hint: `按住 ${hotkeyLabel(asrCfg.hotkey || 'LeftAlt*2')} 说话，松开结束`,
      mode: asrCfg.mode || 'hold',
    },
    asr: {
      enabled: asrCfg.enabled !== false,
      engine: asrCfg.engine || 'sensevoice',
      activeEngine: asrState.activeEngine,
      hotkey: asrCfg.hotkey || 'LeftAlt*2',
      mode: asrCfg.mode || 'hold',
      typeHotkey: asrCfg.typeHotkey || 'LeftCtrl*2',
      typeKeyEnabled: asrCfg.typeKeyEnabled !== false,
      ready: asrState.ready,
      detail: asrState.detail,
      micDevice: typeof p.micDevice === 'string' ? p.micDevice : '',
      devices: asrState.devices,
      activeMicLabel: asrState.activeMicLabel,
    },
    proactive: {
      enabled: p.proactivePeek !== false && cfg.proactive?.enabled !== false,
      allowInGame: cfg.proactive?.allowInGame !== false,
    },
    status: currentStatus,
    startX,
    ...(typeof startY === 'number' ? { startY } : {}),
  };
}

// 桌宠当前气泡语音预计播完的时间戳 (在此期间或正在合成语音时，重复触摸不排队触发第二句回复)
let speakingUntil = 0;

// 2.8s 滑动窗口触摸互动合并器：将连续多次摸头、戳一戳、拎起甩出合并为单次事件，绝不拆分成多条排队
const touchBuffer: {
  pokes: number;
  pets: number;
  throws: number;
  woke: boolean;
  asleep: boolean;
  crashed: boolean;
  timer: NodeJS.Timeout | null;
} = {
  pokes: 0, pets: 0, throws: 0, woke: false, asleep: false, crashed: false, timer: null,
};

function flushTouchBuffer() {
  if (touchBuffer.timer) { clearTimeout(touchBuffer.timer); touchBuffer.timer = null; }
  const { pokes, pets, throws, woke, asleep, crashed } = touchBuffer;
  touchBuffer.pokes = 0; touchBuffer.pets = 0; touchBuffer.throws = 0;
  touchBuffer.woke = false; touchBuffer.asleep = false; touchBuffer.crashed = false;

  if (pokes + pets + throws <= 0 && !crashed) return;

  // 若弥酱当前正在合成语音，或正在气泡里说话，不再排队发送触摸事件（避免说完又来一句）
  const isSynthesizing = typeof currentStatus?.text === 'string' && currentStatus.text.includes('合成语音');
  const isSpeaking = Date.now() < speakingUntil;
  if (isSynthesizing || isSpeaking) {
    log.info(`🪶 [防排队] 弥酱正在${isSynthesizing ? '合成语音' : '说话'}中，本次桌面触摸仅作物理反馈，不再排队追发回复`);
    return;
  }

  const parts: string[] = [];
  if (pokes > 0) {
    parts.push(woke ? '你正睡着，主人戳了戳把你戳醒了' : `主人在桌面上戳了你 ${pokes} 下`);
  }
  if (pets > 0) {
    parts.push(asleep && parts.length === 0 ? '你正睡着，主人温柔地摸了摸你的头' : `主人温柔地摸了摸你的猫耳和脑袋${pets > 1 ? ` (${pets} 次)` : ''}`);
  }
  if (throws > 0 || crashed) {
    parts.push(crashed ? '主人把你拎起来甩了出去，你落地太猛摔得眼冒金星' : '主人把你拎起来在屏幕上甩了出去');
  }
  if (!parts.length) return;

  const desc = parts.join('，随后又');
  log.info(`互动事件投递 (已合并): [touch] ${desc}`);
  void pushEventToBrain({
    event_type: 'touch',
    touch_kind: crashed || throws > 0 ? 'throw' : pets > 0 ? 'pet' : 'poke',
    count: pokes + pets + throws,
    text: desc,
    must_reply: true,
  });
}

function handlePetTouch(msg: Record<string, any>) {
  const kind = String(msg.kind || '');
  if (!['poke', 'pet', 'throw', 'drop', 'crash'].includes(kind)) return;
  if (kind === 'drop') return;
  if (kind === 'crash') {
    touchBuffer.crashed = true;
    touchBuffer.throws = Math.max(1, touchBuffer.throws);
  } else if (kind === 'throw') {
    touchBuffer.throws++;
  } else if (kind === 'pet') {
    touchBuffer.pets++;
  } else if (kind === 'poke') {
    touchBuffer.pokes++;
  }
  if (msg.woke) touchBuffer.woke = true;
  if (msg.asleep) touchBuffer.asleep = true;

  if (touchBuffer.timer) clearTimeout(touchBuffer.timer);
  touchBuffer.timer = setTimeout(flushTouchBuffer, 2800);
}

// 向桌宠弹出确认气泡 (用于 CUA 权限询问)
async function askPetConfirm(question: string, options: string[], timeoutMs = 60_000): Promise<number | null> {
  if (!petWs || petWs.readyState !== petWs.OPEN) {
    // 桌宠窗口未打开时回退至 Win32 置顶系统对话框
    try {
      const ans = await cuaEngine.request<string>({
        op: 'confirm',
        text: question,
        caption: '星见弥 (弥酱) 电脑操作请求',
        yes: options[0] || '可以',
        no: options[options.length - 1] || '这次不行',
        timeoutMs,
      }, timeoutMs + 5000);
      return ans === 'yes' ? 0 : null;
    } catch {
      return null;
    }
  }
  const id = randomUUID();
  return new Promise<number | null>((resolve) => {
    const timer = setTimeout(() => {
      pendingConfirms.delete(id);
      sendToPet({ t: 'ask-close', id });
      resolve(null);
    }, timeoutMs);
    pendingConfirms.set(id, { resolve, timer });
    sendToPet({ t: 'confirm', id, question, options });
  });
}

// 检查 CUA 权限 (四档: ask-each-turn / ask-before-acting / ask-once / never-ask + 终端独立确认)
async function checkCuaPermission(isWriteAction: boolean, isTerminalTarget = false): Promise<{ allowed: boolean; reason?: string }> {
  const cuaCfg = cfg.cua || {};
  if (cuaCfg.enabled === false) {
    return { allowed: false, reason: '电脑操作 (CUA) 总开关当前处于关闭状态' };
  }
  if (isWriteAction && cuaCfg.control === false) {
    return { allowed: false, reason: '当前仅开启了看屏幕权限，鼠标与键盘操作开关处于关闭状态' };
  }

  const level = cuaCfg.permission || 'ask-once';
  const grantMs = (Number(cuaCfg.grantMinutes) || 30) * 60_000;
  const now = Date.now();

  if (isTerminalTarget && !cuaCfg.skipTerminalAsk && now > cuaEngine.terminalGrantedUntil) {
    const idx = await askPetConfirm(
      '弥酱想要在终端/命令行窗口中输入指令，允许她操作终端吗？',
      [`允许 (${cuaCfg.grantMinutes || 30}分钟内免问)`, '仅本次允许', '这次不行']
    );
    if (idx === null || idx === 2) return { allowed: false, reason: '主人拒绝了在终端窗口中的操作请求' };
    if (idx === 0) cuaEngine.terminalGrantedUntil = Date.now() + grantMs;
  }

  if (level === 'never-ask') return { allowed: true };
  if (level === 'ask-before-acting' && !isWriteAction) return { allowed: true };
  if (level === 'ask-once') {
    if (!isWriteAction) return { allowed: true };
    if (now <= cuaEngine.grantedUntil) return { allowed: true };
  }

  const q = isWriteAction
    ? '允许弥酱操作你的鼠标和键盘吗？（你一动鼠标键盘她就会立刻停手让位）'
    : '允许弥酱看一眼你现在的电脑屏幕吗？';
  const idx = await askPetConfirm(q, [`可以 (${cuaCfg.grantMinutes || 30}分钟内免问)`, '仅本次可以', '这次不行']);
  if (idx === null || idx === 2) {
    return { allowed: false, reason: '主人未同意本次电脑屏幕/键鼠操作' };
  }
  if (idx === 0) {
    cuaEngine.grantedUntil = Date.now() + grantMs;
  }
  return { allowed: true };
}

// =========================================================================
// 5. 无钩子全局热键 (LeftAlt*2 说话 / LeftCtrl*2 打字) 与本地语音识别 (ASR)
// =========================================================================
const asrState = {
  ready: false,
  listening: false,
  finishing: false,
  startedAt: 0,
  latched: false,
  activeEngine: 'system' as 'sensevoice' | 'system',
  downloadingModel: false,
  detail: null as string | null,
  activeMicLabel: null as string | null,
  devices: [] as Array<{ id: string; label: string }>,
  preRollFrames: [] as Int16Array[],
  talkWatcher: null as KeyWatcher | null,
  typeWatcher: null as KeyWatcher | null,
  sherpa: null as SherpaAsr | null,
  recognizer: null as SystemRecognizer | null,
  segmenter: null as Segmenter | null,
  openSentence: null as SystemSentence | null,
};

function senseVoiceFilesExist(): boolean {
  try {
    return SENSEVOICE_FILES.every((f) => {
      const p = join(SENSEVOICE_DIR, f.name);
      return existsSync(p) && statSync(p).size >= f.minBytes;
    });
  } catch {
    return false;
  }
}

async function ensureSenseVoiceModel(): Promise<boolean> {
  if (senseVoiceFilesExist()) return true;
  if (asrState.downloadingModel) return false;
  asrState.downloadingModel = true;
  try {
    mkdirSync(SENSEVOICE_DIR, { recursive: true });
    for (const file of SENSEVOICE_FILES) {
      const dest = join(SENSEVOICE_DIR, file.name);
      if (existsSync(dest) && statSync(dest).size >= file.minBytes) continue;
      const tmp = `${dest}.partial`;
      let downloaded = false;
      for (const url of file.urls) {
        try {
          log.info(`📥 [SenseVoice] 正在下载高精度语音模型文件 ${file.name} (${url.includes('modelscope') ? 'ModelScope 国内源' : 'HuggingFace'})...`);
          const res = await fetch(url, { redirect: 'follow' });
          if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
          const total = Number(res.headers.get('content-length')) || file.minBytes;
          let got = 0;
          let lastLogPct = -10;
          const out = createWriteStream(tmp);
          const reader = res.body.getReader();
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            if (value) {
              got += value.byteLength;
              out.write(Buffer.from(value));
              const pct = Math.min(99, Math.round((got / total) * 100));
              if (pct - lastLogPct >= 10) {
                lastLogPct = pct;
                const mbGot = (got / 1048576).toFixed(1);
                const mbTot = (total / 1048576).toFixed(1);
                asrState.detail = `正在下载 SenseVoice 模型: ${pct}% (${mbGot}/${mbTot} MB)`;
                log.info(`📥 [SenseVoice] ${file.name}: ${pct}% (${mbGot} / ${mbTot} MB)`);
                broadcastPrefs(getSnapshot());
              }
            }
          }
          await new Promise<void>((resolve, reject) => {
            out.end(() => resolve());
            out.on('error', reject);
          });
          if (statSync(tmp).size >= file.minBytes) {
            renameSync(tmp, dest);
            downloaded = true;
            break;
          }
        } catch (err) {
          log.warn(`下载 ${file.name} (${url}) 失败: ${(err as Error).message}`);
        }
      }
      if (!downloaded) {
        asrState.detail = 'SenseVoice 模型下载失败，已自动降级使用 Windows SAPI';
        broadcastPrefs(getSnapshot());
        return false;
      }
    }
    log.info('✅ [SenseVoice] 阿里 FunASR SenseVoiceSmall 高精度离线语音模型下载完成！');
    return true;
  } finally {
    asrState.downloadingModel = false;
  }
}

async function startOrSwitchAsrEngine() {
  const asrCfg = cfg.asr || {};
  const wanted = asrCfg.engine || 'sensevoice';

  // 始终先确保 Windows SAPI 兜底可用（在 SenseVoice 下载期间或切换为 system 时零等待工作）
  if (!asrState.recognizer && systemRecognizerSupported()) {
    asrState.recognizer = new SystemRecognizer({
      language: () => asrCfg.language || 'zh',
      culture: () => 'zh-CN',
      timeoutMs: () => 15_000,
      log,
    });
    await asrState.recognizer.start();
  }

  if (wanted === 'sensevoice') {
    if (senseVoiceFilesExist()) {
      if (!asrState.sherpa) {
        asrState.sherpa = new SherpaAsr({
          kind: SENSEVOICE,
          model: () => ({
            paths: {
              model: join(SENSEVOICE_DIR, 'model.int8.onnx'),
              tokens: join(SENSEVOICE_DIR, 'tokens.txt'),
            },
          }),
          language: () => asrCfg.language || 'auto',
          threads: () => 2,
          log,
        });
      }
      await asrState.sherpa.start();
      if (asrState.sherpa.ready) {
        asrState.activeEngine = 'sensevoice';
        asrState.ready = true;
        asrState.detail = 'SenseVoiceSmall (FunASR 高精度本地模型)';
        broadcastPrefs(getSnapshot());
        return;
      }
    } else {
      // 模型尚未就绪：先用 SAPI 顶上，后台自动从 ModelScope 拉取 SenseVoiceSmall 并无缝热切换！
      asrState.activeEngine = 'system';
      asrState.ready = !!asrState.recognizer?.ready;
      asrState.detail = '正在后台下载 SenseVoice 高精度模型（当前由 SAPI 临时顶替）…';
      broadcastPrefs(getSnapshot());
      void ensureSenseVoiceModel().then(async (ok) => {
        if (ok && (cfg.asr?.engine || 'sensevoice') === 'sensevoice') {
          await startOrSwitchAsrEngine();
        }
      });
      return;
    }
  }

  // 使用 Windows SAPI
  asrState.activeEngine = 'system';
  if (asrState.recognizer && !asrState.recognizer.ready) {
    await asrState.recognizer.start();
  }
  asrState.ready = !!asrState.recognizer?.ready;
  asrState.detail = asrState.recognizer?.state().detail || 'Windows SAPI 系统语音识别';
  broadcastPrefs(getSnapshot());
}

async function rebindHotkeys() {
  const asrCfg = cfg.asr || {};
  if (asrState.typeWatcher) {
    try { asrState.typeWatcher.stop(); } catch {}
    asrState.typeWatcher = null;
  }
  if (asrState.talkWatcher) {
    try { asrState.talkWatcher.stop(); } catch {}
    asrState.talkWatcher = null;
  }

  // 1. 绑定打字快捷键 (默认 LeftCtrl*2 双击唤醒打字气泡)
  if (asrCfg.typeKeyEnabled !== false) {
    const typeKeyStr = String(asrCfg.typeHotkey || 'LeftCtrl*2').trim();
    const parsedType = parseHotkey(typeKeyStr);
    if (parsedType) {
      const w = await watchHotkey(parsedType, (down) => {
        if (down) sendToPet({ t: 'type' });
      }, 30, undefined, undefined, 'tap');
      if (typeof w !== 'string') {
        asrState.typeWatcher = w;
        log.info(`已绑定无钩子打字快捷键: ${typeKeyStr}`);
      } else {
        log.warn(`打字快捷键 (${typeKeyStr}) 绑定失败: ${w}`);
      }
    }
  }

  // 2. 绑定说话快捷键 (默认 hold: 双击 LeftAlt 开启录制后，松开第二次 LeftAlt 时立即结束录音)
  const talkKeyStr = String(asrCfg.hotkey || 'LeftAlt*2').trim();
  const parsedTalk = parseHotkey(talkKeyStr);
  if (parsedTalk) {
    const w = await watchHotkey(
      parsedTalk,
      (down) => {
        if (cfg.asr?.enabled === false) return;
        const mode = cfg.asr?.mode || 'hold';
        if (down) {
          if (asrState.listening && asrState.latched) {
            void finishVoiceHold();
          } else if (!asrState.listening) {
            startVoiceHold();
          }
        } else {
          if (!asrState.listening) return;
          if (mode === 'hold') {
            // 默认 hold 模式：双击开启录制后，松开第二次按键时立即结束录音！
            void finishVoiceHold();
          } else if (mode === 'auto') {
            const heldMs = Date.now() - asrState.startedAt;
            if (heldMs >= 350 && !asrState.latched) {
              void finishVoiceHold();
            } else {
              asrState.latched = true;
            }
          } else {
            asrState.latched = true;
          }
        }
      },
      30,
      () => {
        if (cfg.asr?.enabled === false) return;
        if (asrState.listening && asrState.latched) {
          void finishVoiceHold();
          return;
        }
        sendToPet({ t: 'listen', phase: 'ready' });
      },
      undefined,
      'press'
    );
    if (typeof w !== 'string') {
      asrState.talkWatcher = w;
      log.info(`已绑定无钩子说话快捷键 (GetAsyncKeyState 30ms 轮询, 模式=${asrCfg.mode || 'hold'}): ${talkKeyStr}`);
    } else {
      log.warn(`说话快捷键 (${talkKeyStr}) 绑定失败: ${w}`);
    }
  }
}

async function initHotkeysAndAsr() {
  const asrCfg = cfg.asr || {};
  await rebindHotkeys();

  if (asrCfg.enabled !== false) {
    await startOrSwitchAsrEngine();
  }
}

function startVoiceHold() {
  // 若上一次还有残留未关闭的句子，先安全取消，彻底杜绝跨次死锁
  if (asrState.openSentence) {
    try { asrState.openSentence.cancel?.(); } catch {}
    asrState.openSentence = null;
  }

  asrState.listening = true;
  asrState.finishing = false;
  asrState.startedAt = Date.now();
  asrState.latched = (cfg.asr?.mode === 'toggle');
  sendToPet({ t: 'talk-key', down: true });
  sendToPet({ t: 'listen', phase: 'start' });
  broadcastPrefs({ mic: true });

  try {
    const onPartial = (partialText: string) => {
      if (asrState.listening || asrState.finishing) {
        sendToPet({ t: 'listen', phase: 'partial', text: '', interim: partialText });
      }
    };
    const useSenseVoice = (cfg.asr?.engine || 'sensevoice') === 'sensevoice' && asrState.sherpa?.ready;
    const sentence = useSenseVoice
      ? asrState.sherpa!.sentence(onPartial)
      : asrState.recognizer?.ready
        ? asrState.recognizer.sentence(onPartial)
        : null;

    if (sentence) {
      // 将按下快捷键前 400ms 的热麦预录环形缓冲帧率先灌入识别器，杜绝开头吞字！
      if (asrState.preRollFrames.length > 0) {
        for (const frame of asrState.preRollFrames) {
          sentence.write(frame);
        }
        asrState.preRollFrames = [];
      }
      asrState.openSentence = sentence;
    } else {
      void startOrSwitchAsrEngine();
    }
  } catch (e) {
    log.warn(`启动语音听写会话失败: ${(e as Error).message}`);
    asrState.openSentence = null;
  }
}

async function finishVoiceHold() {
  if (!asrState.listening || asrState.finishing) return;
  asrState.finishing = true;
  asrState.latched = false;
  sendToPet({ t: 'talk-key', down: false });
  sendToPet({ t: 'listen', phase: 'transcribing' });

  // 松开第二次 Alt 时，多收 180ms 的尾音缓冲帧（防止 WebAudio Worklet 最后一包语音在路上被截断），同时保持热麦常开
  await new Promise((r) => setTimeout(r, 180));
  asrState.listening = false;
  asrState.finishing = false;

  const open = asrState.openSentence;
  asrState.openSentence = null;
  if (!open) {
    sendToPet({ t: 'listen', phase: 'empty' });
    return;
  }
  try {
    const res = await open.end();
    const text = (res.text || '').trim();
    if (!text) {
      log.info('🎙️ 录音已结束（未检测到有效语音文本）');
      sendToPet({ t: 'listen', phase: 'empty' });
      return;
    }
    log.info(`🎙️ 语音输入识别完成: "${text}"`);
    sendToPet({ t: 'listen', phase: 'heard', text });
    await pushEventToBrain({
      event_type: 'speech',
      text,
      interrupt: true,
      must_reply: true,
    });
  } catch (e) {
    log.warn(`语音识别异常: ${(e as Error).message}`);
    sendToPet({ t: 'listen', phase: 'empty' });
  }
}

// =========================================================================
// 6. 0-Token 伴随式屏幕感知 + 游戏局内/日常主动吐槽探针 (Proactive Watcher)
// =========================================================================
interface WatcherState {
  lastApp: string;
  lastTitle: string;
  windowEnteredAt: number;
  lastPeekAt: number;
  lastSceneSwitchPeekAt: number;
  inGame: boolean;
  idleSec: number;
  peeking: boolean;
}

const watcherState: WatcherState = {
  lastApp: '',
  lastTitle: '',
  windowEnteredAt: Date.now(),
  lastPeekAt: Date.now() - 120_000,
  lastSceneSwitchPeekAt: 0,
  inGame: false,
  idleSec: 0,
  peeking: false,
};

function isGameWindow(app: string | null, title: string | null): boolean {
  const a = (app || '').trim();
  const t = (title || '').trim();
  return GAME_PROCESS_RE.test(a) || GAME_TITLE_RE.test(t);
}

async function triggerProactiveScreenPeek(reason: string, force = false): Promise<{ triggered: boolean; reason: string }> {
  if (watcherState.peeking) return { triggered: false, reason: 'already_peeking' };
  const proactiveOn = cfg.prefs?.proactivePeek !== false && cfg.proactive?.enabled !== false;
  if (!force && !proactiveOn) return { triggered: false, reason: 'proactive_disabled' };

  watcherState.peeking = true;
  try {
    const shotCfg = cfg.cua?.screenshot || { maxWidth: 1280, maxHeight: 800, quality: 78 };
    const shot = await cuaEngine.request<ScreenshotResult>({
      op: 'screenshot',
      maxWidth: shotCfg.maxWidth || 1280,
      maxHeight: shotCfg.maxHeight || 800,
      quality: shotCfg.quality || 78,
    });
    cuaEngine.latestScreen = shot;
    cuaEngine.latestView = { display: shot.display, layout: shot.layout, width: shot.width, height: shot.height, scale: shot.scale };
    watcherState.lastPeekAt = Date.now();

    const jpegBase64 = toBuffer(shot.jpeg).toString('base64');
    log.info(`👀 触发主动看屏吐槽 (${reason}) | 窗口: [${shot.foregroundApp || '未知'}] ${shot.foreground || ''}`);

    const ok = await pushEventToBrain({
      event_type: 'screen_peek',
      reason,
      in_game: watcherState.inGame,
      foreground_app: shot.foregroundApp || '',
      foreground_title: shot.foreground || '',
      display: shot.display,
      width: shot.width,
      height: shot.height,
      jpeg_base64: jpegBase64,
      must_reply: force,
    });
    return { triggered: ok, reason };
  } catch (e) {
    log.warn(`主动看屏截屏失败: ${(e as Error).message}`);
    return { triggered: false, reason: (e as Error).message };
  } finally {
    watcherState.peeking = false;
  }
}

function startProactiveWatcher() {
  setInterval(async () => {
    try {
      const info = await cuaEngine.request<ScreenInfo>({ op: 'info' }, 5000);
      cuaEngine.latestScreen = info;
      const app = (info.foregroundApp || '').trim();
      const title = (info.foreground || '').trim();
      const now = Date.now();

      if (app !== watcherState.lastApp || title !== watcherState.lastTitle) {
        const prevApp = watcherState.lastApp;
        watcherState.lastApp = app;
        watcherState.lastTitle = title;
        watcherState.windowEnteredAt = now;
        watcherState.inGame = isGameWindow(app, title);

        // 检查是否发生重要场景切换（如刚刚打开游戏、视频或切到新软件）
        const proactiveOn = cfg.prefs?.proactivePeek !== false && cfg.proactive?.enabled !== false;
        const switchCoolMs = (Number(cfg.proactive?.sceneSwitchCooldownSec) || 180) * 1000;
        if (
          proactiveOn &&
          prevApp &&
          app &&
          prevApp.toLowerCase() !== app.toLowerCase() &&
          !/(explorer\.exe|electron\.exe|searchhost\.exe|shellexperiencehost\.exe)/i.test(app) &&
          now - watcherState.lastPeekAt >= switchCoolMs
        ) {
          if (watcherState.inGame && cfg.proactive?.allowInGame !== false) {
            void triggerProactiveScreenPeek(`主人刚刚切进了游戏【${title || app}】`);
            return;
          }
        }
      } else {
        watcherState.inGame = isGameWindow(app, title);
      }

      const proactiveOn = cfg.prefs?.proactivePeek !== false && cfg.proactive?.enabled !== false;
      if (!proactiveOn || !app) return;

      const elapsedSincePeekSec = (now - watcherState.lastPeekAt) / 1000;
      const staySec = (now - watcherState.windowEnteredAt) / 1000;

      // 1. 游戏局内实时战况吐槽 (允许局内吐槽开启时，默认每 4 分钟看一眼战况)
      if (watcherState.inGame && cfg.proactive?.allowInGame !== false) {
        const gameInterval = Number(cfg.proactive?.gameIntervalSec) || 240;
        if (elapsedSincePeekSec >= gameInterval && staySec >= 25) {
          void triggerProactiveScreenPeek(`主人正在玩游戏【${title || app}】（局内实时战况吐槽）`);
          return;
        }
      }

      // 2. 日常陪伴/写代码/看网页定时主动看屏吐槽 (默认每 15 分钟一次)
      const normalInterval = Number(cfg.proactive?.normalIntervalSec) || 900;
      if (elapsedSincePeekSec >= normalInterval && staySec >= 60) {
        const hour = new Date().getHours();
        const tag = (hour >= 0 && hour < 5)
          ? `深夜 ${hour} 点主人还在用【${title || app}】熬夜`
          : CODING_PROCESS_RE.test(app)
            ? `主人正在【${title || app}】专注写代码/干活`
            : `主人正在使用【${title || app}】`;
        void triggerProactiveScreenPeek(tag);
      }
    } catch {
      // 忽略单次采样超时
    }
  }, 3500);
}

// =========================================================================
// 7. 处理来自 qqAgent (MCP 工具与 WebUI) 的 /api/mcp/* 请求
// =========================================================================
async function handleMcpApi(req: IncomingMessage, res: ServerResponse, path: string): Promise<void> {
  const auth = req.headers.authorization || '';
  const isLocal = req.socket.remoteAddress === '127.0.0.1' || req.socket.remoteAddress === '::1' || req.socket.remoteAddress === '::ffff:127.0.0.1';
  if (!isLocal && auth !== `Bearer ${AUTH_TOKEN}`) {
    json(res, 401, { ok: false, error: 'unauthorized' });
    return;
  }

  if (req.method === 'GET' && path === '/api/mcp/state') {
    const pack = currentPack();
    json(res, 200, {
      ok: true,
      petConnected: !!petWs && petWs.readyState === petWs.OPEN,
      petIsWindow,
      figure: pack.id,
      figureName: pack.manifest.name?.zh || pack.id,
      prefs: cfg.prefs,
      proactive: {
        ...cfg.proactive,
        enabled: cfg.prefs?.proactivePeek !== false && cfg.proactive?.enabled !== false,
        inGame: watcherState.inGame,
        foregroundApp: watcherState.lastApp,
        foregroundTitle: watcherState.lastTitle,
        lastPeekAt: watcherState.lastPeekAt,
      },
      cua: {
        ...cfg.cua,
        grantedRemainingSec: Math.max(0, Math.round((cuaEngine.grantedUntil - Date.now()) / 1000)),
      },
      asr: {
        ...cfg.asr,
        ready: asrState.ready,
        detail: asrState.detail,
      },
      screen: cuaEngine.latestScreen,
    });
    return;
  }

  const bodyStr = await readBody(req, 12 * 1024 * 1024);
  const body = bodyStr ? parseJson(bodyStr) : {};
  if (!body) {
    json(res, 400, { ok: false, error: 'invalid_json' });
    return;
  }

  // 7.1 冒气泡说话 + 9D 真声播放 (/api/mcp/say)
  if (req.method === 'POST' && path === '/api/mcp/say') {
    const scriptText = String(body.script || '').trim();
    if (!scriptText) {
      json(res, 400, { ok: false, error: 'script is empty' });
      return;
    }
    const pack = currentPack();
    const { beats, dropped } = parseScript(scriptText, pack.vocab);

    // 若调用方附带了预合成的 WAV Base64 列表或单段 WAV Base64，挂载到有文本的 beat 上
    const wavList: string[] = Array.isArray(body.wav_base64_list)
      ? body.wav_base64_list
      : (typeof body.wav_base64 === 'string' && body.wav_base64 ? [body.wav_base64] : []);

    let wavIdx = 0;
    let totalAudioDuration = 0;
    const enrichedBeats = beats.map((b) => {
      if (b.text && wavIdx < wavList.length && wavList[wavIdx]) {
        try {
          const wavBuf = Buffer.from(wavList[wavIdx]!, 'base64');
          wavIdx++;
          const stored = storeTtsWav(wavBuf);
          totalAudioDuration += stored.duration;
          return { ...b, audioUrl: stored.url, audioDuration: stored.duration };
        } catch {}
      }
      return b;
    });

    // 正式说出气泡前，自动清除“在合成语音…”等工作状态气泡，并登记本次语音气泡持续时长
    currentStatus = null;
    sendToPet({ t: 'status', status: null });
    const id = randomUUID();
    const sent = sendToPet({ t: 'say', id, beats: enrichedBeats });
    const estSec = Math.max(
      estimateSeconds(beats, Number(cfg.prefs?.bubbleSpeed) || 1, undefined, Number(cfg.prefs?.sayBubbleHoldSec) || 0),
      Math.round(totalAudioDuration * 10) / 10
    );
    speakingUntil = Date.now() + estSec * 1000;
    json(res, 200, {
      ok: true,
      sent,
      id,
      beatsCount: enrichedBeats.length,
      estimatedSeconds: estSec,
      dropped,
    });
    return;
  }

  // 7.2 提问气泡 (/api/mcp/ask)
  if (req.method === 'POST' && path === '/api/mcp/ask') {
    const question = String(body.question || '').trim();
    const options = Array.isArray(body.options) ? body.options.map(String).slice(0, 4) : [];
    const own = body.allow_own_answer !== false;
    const id = randomUUID();
    const sent = sendToPet({ t: 'ask', id, question, options, own });
    json(res, 200, { ok: true, sent, id });
    return;
  }

  // 7.3 做动作/表情 (/api/mcp/act)
  if (req.method === 'POST' && path === '/api/mcp/act') {
    const rawList = Array.isArray(body.actions) ? body.actions : String(body.actions || '').split(/[,，、\s]+/).filter(Boolean);
    const pack = currentPack();
    const { actions, dropped } = parseActions(rawList, pack.vocab);
    const sent = actions.length ? sendToPet({ t: 'act', actions }) : false;
    json(res, 200, { ok: true, sent, actions, dropped });
    return;
  }

  // 7.4 走动/跑动 (/api/mcp/move)
  if (req.method === 'POST' && path === '/api/mcp/move') {
    const rawTo = String(body.to ?? 'center').trim().toLowerCase();
    const run = !!body.run;
    let to: string | number = 0.5;
    if (rawTo === 'left' || rawTo === '左' || rawTo === '左边') to = 0.12;
    else if (rawTo === 'center' || rawTo === 'middle' || rawTo === '中' || rawTo === '中间') to = 0.5;
    else if (rawTo === 'right' || rawTo === '右' || rawTo === '右边') to = 0.88;
    else if (rawTo === 'cursor' || rawTo === '鼠标' || rawTo === '指针') to = 'cursor';
    else {
      const n = Number(rawTo);
      to = Number.isFinite(n) ? Math.max(0.05, Math.min(0.95, n > 1 ? n / 100 : n)) : 0.5;
    }
    const id = randomUUID();
    const sent = sendToPet({ t: 'walk', id, to, run });
    json(res, 200, { ok: true, sent, id, to, run });
    return;
  }

  // 7.5 头顶工作状态泡更新 (/api/mcp/status)
  if (req.method === 'POST' && path === '/api/mcp/status') {
    currentStatus = body.status ?? null;
    sendToPet({ t: 'status', status: currentStatus });
    json(res, 200, { ok: true });
    return;
  }

  // 7.6 桌宠设置与主动吐槽开关热更新 (/api/mcp/prefs)
  if (req.method === 'POST' && path === '/api/mcp/prefs') {
    cfg.prefs ??= {};
    if (typeof body.proactivePeek === 'boolean') {
      cfg.prefs.proactivePeek = body.proactivePeek;
      cfg.proactive ??= {};
      cfg.proactive.enabled = body.proactivePeek;
    }
    if (typeof body.allowInGame === 'boolean') {
      cfg.proactive ??= {};
      cfg.proactive.allowInGame = body.allowInGame;
    }
    if (typeof body.normalIntervalSec === 'number') {
      cfg.proactive ??= {};
      cfg.proactive.normalIntervalSec = Math.max(30, body.normalIntervalSec);
    }
    if (typeof body.gameIntervalSec === 'number') {
      cfg.proactive ??= {};
      cfg.proactive.gameIntervalSec = Math.max(30, body.gameIntervalSec);
    }
    for (const k of ['roam', 'sound', 'theme', 'scale', 'bubbleSize', 'fontSize', 'bubbleSpeed', 'passWhenFullscreen', 'hideWhenFullscreen', 'hideFromCapture', 'perchWindows', 'clickThrough']) {
      if (k in body) cfg.prefs[k] = body[k];
    }
    cfg.asr ??= {};
    let hotkeysChanged = false;
    if (typeof body.talkHotkey === 'string' && body.talkHotkey.trim() && parseHotkey(body.talkHotkey.trim())) {
      cfg.asr.hotkey = body.talkHotkey.trim();
      hotkeysChanged = true;
    }
    if (typeof body.talkMode === 'string' && ['auto', 'toggle', 'hold'].includes(body.talkMode)) {
      cfg.asr.mode = body.talkMode;
      hotkeysChanged = true;
    }
    if (typeof body.typeHotkey === 'string' && body.typeHotkey.trim() && parseHotkey(body.typeHotkey.trim())) {
      cfg.asr.typeHotkey = body.typeHotkey.trim();
      hotkeysChanged = true;
    }
    if (typeof body.typeKeyEnabled === 'boolean') {
      cfg.asr.typeKeyEnabled = body.typeKeyEnabled;
      hotkeysChanged = true;
    }
    if (hotkeysChanged) {
      void rebindHotkeys();
    }
    if (body.cua && typeof body.cua === 'object') {
      cfg.cua = { ...(cfg.cua || {}), ...body.cua };
    }
    if (body.skin && typeof body.skin === 'object') {
      cfg.skin = { ...(cfg.skin || {}), ...body.skin };
    }
    saveConfig();
    broadcastPrefs(getSnapshot());
    json(res, 200, { ok: true, prefs: cfg.prefs, proactive: cfg.proactive, cua: cfg.cua });
    return;
  }

  // 7.7 立即触发一次主动看屏吐槽 (/api/mcp/peek_now)
  if (req.method === 'POST' && path === '/api/mcp/peek_now') {
    const out = await triggerProactiveScreenPeek(String(body.reason || '主人手动召唤看屏吐槽'), true);
    json(res, 200, { ok: out.triggered, ...out });
    return;
  }

  // 7.8 屏幕分析与电脑操作 CUA (/api/mcp/cua)
  if (req.method === 'POST' && path === '/api/mcp/cua') {
    const op = String(body.op || 'screenshot');
    const isWrite = !['info', 'screenshot', 'zoom', 'windows', 'wait'].includes(op);

    // 检查目标是否为终端或 Win+R
    const fg = cuaEngine.latestScreen;
    const termTarget = isWrite && (
      isTerminal(fg?.foreground ?? null, fg?.foregroundApp ?? null) ||
      (op === 'key' && opensRunBox(String(body.keys || '')))
    );

    const perm = await checkCuaPermission(isWrite, termTarget);
    if (!perm.allowed) {
      json(res, 200, { ok: false, permitted: false, error: perm.reason });
      return;
    }

    const shotCfg = cfg.cua?.screenshot || { maxWidth: 1280, maxHeight: 800, quality: 78, afterAction: true, settleMs: 500 };
    const yieldCfg = { idleMs: Number(cfg.cua?.userIdleMs) ?? 2000, maxWaitMs: Number(cfg.cua?.maxYieldWaitMs) ?? 15_000 };

    try {
      if (op === 'info') {
        const info = await cuaEngine.request<ScreenInfo>({ op: 'info' });
        cuaEngine.latestScreen = info;
        json(res, 200, { ok: true, info });
        return;
      }

      if (op === 'screenshot') {
        sendToPet({ t: 'status', status: { kind: 'look', text: '在看屏幕' } });
        const disp = typeof body.display === 'number' && body.display > 0 ? body.display : undefined;
        const shot = await cuaEngine.request<ScreenshotResult>({
          op: 'screenshot',
          display: disp,
          maxWidth: shotCfg.maxWidth || 1280,
          maxHeight: shotCfg.maxHeight || 800,
          quality: shotCfg.quality || 78,
        });
        cuaEngine.latestScreen = shot;
        cuaEngine.latestView = { display: shot.display, layout: shot.layout, width: shot.width, height: shot.height, scale: shot.scale };
        json(res, 200, {
          ok: true,
          display: shot.display,
          width: shot.width,
          height: shot.height,
          scale: shot.scale,
          displays: shot.displays,
          cursor: shot.cursor,
          foreground: shot.foreground,
          foregroundApp: shot.foregroundApp,
          jpeg_base64: toBuffer(shot.jpeg).toString('base64'),
        });
        return;
      }

      if (op === 'zoom') {
        if (!cuaEngine.latestView) {
          json(res, 200, { ok: false, error: '请先调用 pet_cua_screenshot 截取全屏后再使用 pet_cua_zoom 局部放大' });
          return;
        }
        sendToPet({ t: 'status', status: { kind: 'look', text: '在放大细看屏幕' } });
        const scale = cuaEngine.latestView.scale || 1;
        const z = await cuaEngine.request<ZoomResult>({
          op: 'zoom',
          at: { display: cuaEngine.latestView.display, layout: cuaEngine.latestView.layout },
          x: Math.round(Number(body.x || 0) / scale),
          y: Math.round(Number(body.y || 0) / scale),
          width: Math.max(16, Math.round(Number(body.width || 320) / scale)),
          height: Math.max(16, Math.round(Number(body.height || 240) / scale)),
          maxWidth: shotCfg.maxWidth || 1280,
          maxHeight: shotCfg.maxHeight || 800,
          quality: 88,
        });
        json(res, 200, {
          ok: true,
          width: z.width,
          height: z.height,
          jpeg_base64: toBuffer(z.jpeg).toString('base64'),
        });
        return;
      }

      if (op === 'windows') {
        sendToPet({ t: 'status', status: { kind: 'look', text: '在看开着的窗口' } });
        const wins = await cuaEngine.request<WindowEntry[]>({ op: 'windows' });
        json(res, 200, { ok: true, windows: wins });
        return;
      }

      if (op === 'wait') {
        const sec = Math.max(0.5, Math.min(30, Number(body.seconds) || 2));
        sendToPet({ t: 'status', status: { kind: 'wait', text: '在等待', endsAt: Date.now() + sec * 1000 } });
        await new Promise((r) => setTimeout(r, sec * 1000));
        const shot = await cuaEngine.request<ScreenshotResult>({
          op: 'screenshot',
          display: cuaEngine.latestView?.display,
          maxWidth: shotCfg.maxWidth || 1280,
          maxHeight: shotCfg.maxHeight || 800,
          quality: shotCfg.quality || 78,
        });
        cuaEngine.latestScreen = shot;
        cuaEngine.latestView = { display: shot.display, layout: shot.layout, width: shot.width, height: shot.height, scale: shot.scale };
        json(res, 200, {
          ok: true,
          waitedSeconds: sec,
          foreground: shot.foreground,
          jpeg_base64: toBuffer(shot.jpeg).toString('base64'),
        });
        return;
      }

      // 其余写操作 (click / move / drag / mouseDown / mouseUp / scroll / type / key / focus)
      let actionRes: any = null;
      if (op === 'focus') {
        sendToPet({ t: 'status', status: { kind: 'click', text: '在切窗口' } });
        const wins = await cuaEngine.request<WindowEntry[]>({ op: 'windows' });
        const q = String(body.window || '').trim().toLowerCase();
        const found = wins.find((w) => w.handle.toLowerCase() === q || w.title.toLowerCase().includes(q) || (w.app && w.app.toLowerCase().includes(q)));
        if (!found) {
          json(res, 200, { ok: false, error: `未找到匹配的窗口: "${body.window}"` });
          return;
        }
        actionRes = await cuaEngine.request({ op: 'focus', handle: found.handle, yield: yieldCfg });
      } else if (op === 'click' || op === 'move' || op === 'mouseDown' || op === 'scroll') {
        const { target, px, py } = cuaEngine.toDisplayCoords(Number(body.x || 0), Number(body.y || 0));
        const btn: Button = body.button === 'right' || body.button === 'middle' ? body.button : 'left';
        if (op === 'click') {
          const clicks = Math.max(1, Math.min(3, Number(body.clicks) || 1));
          sendToPet({ t: 'status', status: { kind: 'click', text: clicks === 2 ? '在双击' : btn === 'right' ? '在右键' : '在点' } });
          actionRes = await cuaEngine.request<InputResult>({ op: 'click', at: target, x: px, y: py, button: btn, count: clicks, yield: yieldCfg });
        } else if (op === 'move') {
          sendToPet({ t: 'status', status: { kind: 'click', text: '在挪鼠标' } });
          actionRes = await cuaEngine.request<InputResult>({ op: 'move', at: target, x: px, y: py, yield: yieldCfg });
        } else if (op === 'mouseDown') {
          sendToPet({ t: 'status', status: { kind: 'click', text: '在按住鼠标' } });
          actionRes = await cuaEngine.request<InputResult>({ op: 'mouseDown', at: target, x: px, y: py, button: btn, yield: yieldCfg });
        } else {
          sendToPet({ t: 'status', status: { kind: 'scroll', text: '在滚动' } });
          actionRes = await cuaEngine.request<InputResult>({
            op: 'scroll', at: target, x: px, y: py,
            down: Number(body.down) || 0, right: Number(body.right) || 0, yield: yieldCfg,
          });
        }
      } else if (op === 'mouseUp') {
        const btn: Button = body.button === 'right' || body.button === 'middle' ? body.button : 'left';
        actionRes = await cuaEngine.request<InputResult>({ op: 'mouseUp', button: btn, yield: yieldCfg });
      } else if (op === 'drag') {
        sendToPet({ t: 'status', status: { kind: 'click', text: '在拖' } });
        const p1 = cuaEngine.toDisplayCoords(Number(body.x || 0), Number(body.y || 0));
        const p2 = cuaEngine.toDisplayCoords(Number(body.to_x || 0), Number(body.to_y || 0));
        actionRes = await cuaEngine.request<InputResult>({
          op: 'drag', at: p1.target, x1: p1.px, y1: p1.py, x2: p2.px, y2: p2.py, yield: yieldCfg,
        });
      } else if (op === 'type') {
        sendToPet({ t: 'status', status: { kind: 'type', text: '在打字' } });
        actionRes = await cuaEngine.request<TypeResult>({
          op: 'type',
          text: String(body.text || ''),
          chunkDelayMs: Number(cfg.cua?.typeChunkDelayMs) || 20,
          yield: yieldCfg,
        });
      } else if (op === 'key') {
        const rawKeys = String(body.keys || '').trim();
        const chords = parseKeys(rawKeys);
        if (!chords) {
          json(res, 200, { ok: false, error: `无法解析按键序列: "${rawKeys}"` });
          return;
        }
        sendToPet({ t: 'status', status: { kind: 'type', text: `在按 · ${rawKeys}` } });
        const holdSec = Number(body.hold_seconds) || 0;
        if (holdSec > 0 && chords.length === 1) {
          actionRes = await cuaEngine.request<HoldResult>({
            op: 'hold', keys: chords[0]!, holdMs: Math.min(30_000, Math.round(holdSec * 1000)), yield: yieldCfg,
          });
        } else {
          actionRes = await cuaEngine.request<InputResult>({ op: 'key', chords, yield: yieldCfg });
        }
      } else {
        json(res, 400, { ok: false, error: `未知的 CUA 操作: ${op}` });
        return;
      }

      // 检查是否因人类正在动鼠标键盘而主动让位
      if (actionRes?.yielded || actionRes?.stoppedBy === 'user') {
        json(res, 200, {
          ok: false,
          yielded: true,
          typed: actionRes.typed,
          error: actionRes.stoppedBy === 'user'
            ? `打字途中使用者动了鼠标或键盘，已立即停手让位（已打出 ${actionRes.typed ?? 0} 个字符）`
            : '使用者正在使用鼠标或键盘（等待静止超时），弥酱已自动让位未执行该操作',
          activity: actionRes.activity,
        });
        return;
      }

      // 动作完成后默认等待 settleMs (500ms) 并附带一张确认截图
      let postShot: ScreenshotResult | null = null;
      if (body.screenshot !== false && shotCfg.afterAction !== false) {
        await new Promise((r) => setTimeout(r, Number(shotCfg.settleMs) || 500));
        postShot = await cuaEngine.request<ScreenshotResult>({
          op: 'screenshot',
          display: cuaEngine.latestView?.display,
          maxWidth: shotCfg.maxWidth || 1280,
          maxHeight: shotCfg.maxHeight || 800,
          quality: shotCfg.quality || 78,
        });
        cuaEngine.latestScreen = postShot;
        cuaEngine.latestView = { display: postShot.display, layout: postShot.layout, width: postShot.width, height: postShot.height, scale: postShot.scale };
      }

      json(res, 200, {
        ok: true,
        op,
        result: actionRes,
        ...(postShot ? {
          display: postShot.display,
          width: postShot.width,
          height: postShot.height,
          scale: postShot.scale,
          foreground: postShot.foreground,
          foregroundApp: postShot.foregroundApp,
          cursor: postShot.cursor,
          jpeg_base64: toBuffer(postShot.jpeg).toString('base64'),
        } : {}),
      });
    } catch (e) {
      json(res, 200, { ok: false, error: (e as Error).message });
    } finally {
      sendToPet({ t: 'status', status: null });
    }
    return;
  }

  json(res, 404, { ok: false, error: 'not_found' });
}

// =========================================================================
// 8. 本地 HTTP + WebSocket 桌宠服务 (7797) 与 Electron 窗口拉起
// =========================================================================
function startServer(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer(async (req, res) => {
      try {
        const url = new URL(req.url ?? '/', `http://${req.headers.host || '127.0.0.1'}`);
        const path = url.pathname;

        // 1. 供 qqAgent (MCP 工具与 WebUI) 调用的接口
        if (path.startsWith('/api/mcp/')) {
          await handleMcpApi(req, res, path);
          return;
        }

        // 2. 内存 TTS WAV 音频流服务
        if (req.method === 'GET' && path.startsWith('/api/tts/') && path.endsWith('.wav')) {
          const id = path.slice('/api/tts/'.length, -4);
          const item = ttsAudioCache.get(id);
          if (!item) { res.writeHead(404).end(); return; }
          res.writeHead(200, {
            'content-type': 'audio/wav',
            'content-length': String(item.buf.length),
            'cache-control': 'no-store',
          });
          res.end(item.buf);
          return;
        }

        // 3. 形象包列表与状态
        if (req.method === 'GET' && path === '/api/figures') {
          json(res, 200, PACKS.map((p) => {
            const m = p.manifest;
            return {
              id: p.id,
              base: p.base,
              name: m.name,
              thumb: m.thumb ?? null,
              entry: m.entry,
              export: m.export,
              model: m.model ?? null,
              axes: m.axes || [],
              presets: m.presets || [],
              vocab: p.vocab.map(({ id: w, kind, seconds, lasting }) => ({ id: w, kind, seconds, ...(lasting ? { lasting } : {}) })),
              sounds: m.sounds || {},
              can: m.can || { walk: true },
              needs: m.needs || [],
              voice: m.voice || { pitch: 1 },
              effects: m.effects || { reach: 0 },
              bubble: m.bubble ?? null,
              removable: false,
            };
          }));
          return;
        }
        if (req.method === 'GET' && path === '/api/figures/status') {
          json(res, 200, { importable: false, large: 134217728, depth: 3, problems: [] });
          return;
        }

        // 4. 沙箱化形象渲染 Frame
        if (req.method === 'GET' && path === '/figure-frame') {
          const self = `http://${req.headers.host}`;
          await sendFile(res, join(WEB_DIR, 'figure-frame.html'), [
            'sandbox allow-scripts', "default-src 'none'", `script-src ${self}`, `img-src ${self} data: blob:`,
            "style-src 'unsafe-inline'", "connect-src 'none'",
            `frame-ancestors ${self} http://127.0.0.1:* http://localhost:*`,
          ].join('; '));
          return;
        }

        // 5. 状态、收藏形象与偏好保存
        if (req.method === 'GET' && path === '/api/state') {
          json(res, 200, getSnapshot());
          return;
        }
        if (req.method === 'GET' && path === '/api/looks') {
          json(res, 200, Array.isArray(cfg.looks) ? cfg.looks : []);
          return;
        }
        if (req.method === 'POST' && path === '/api/looks') {
          const msg = parseJson((await readBody(req)) || '');
          if (Array.isArray(msg?.looks)) {
            cfg.looks = msg.looks;
            saveConfig();
          }
          json(res, 200, Array.isArray(cfg.looks) ? cfg.looks : []);
          return;
        }
        if (req.method === 'POST' && (path === '/api/skin' || path === '/api/prefs')) {
          const msg = parseJson((await readBody(req)) || '');
          if (!msg) { json(res, 400, { error: 'bad json' }); return; }
          if (path === '/api/skin') {
            cfg.skin = { ...(cfg.skin || {}), ...(msg.skin || {}) };
            if (typeof msg.scale === 'number') {
              cfg.prefs ??= {};
              cfg.prefs.scale = msg.scale;
            }
          } else {
            applyClientPrefs(msg);
          }
          saveConfig();
          broadcastPrefs(getSnapshot());
          json(res, 200, { ok: true });
          return;
        }

        if (req.method === 'GET' && path === '/') {
          res.writeHead(302, { location: '/pet' }).end();
          return;
        }

        const file = PAGES[path] ?? (path.startsWith('/web/') ? path.slice(5) : null);
        if (!file) { res.writeHead(404).end(); return; }
        const root = normalize(WEB_DIR).replace(/[\\/]+$/, '') + sep;
        const full = normalize(join(root, decodeURIComponent(file)));
        if (!full.startsWith(root)) { res.writeHead(403).end(); return; }

        if (PAGES[path]) {
          const html = readFileSync(full, 'utf8').replace(/<html lang="[^"]*"/, '<html lang="zh-CN"');
          res.writeHead(200, { 'content-type': MIME['.html'], 'cache-control': 'no-cache', 'content-security-policy': PAGE_CSP });
          res.end(html);
          return;
        }
        await sendFile(res, full, null, path.startsWith('/web/') && req.headers.origin === 'null');
      } catch (e) {
        log.error(`HTTP 请求处理异常: ${(e as Error).message}`);
        if (!res.headersSent) res.writeHead(500).end();
      }
    });

    const wss = new WebSocketServer({ noServer: true });
    server.on('upgrade', (req, socket, head) => {
      const url = new URL(req.url ?? '/', 'http://127.0.0.1');
      if (url.pathname !== '/socket') { socket.destroy(); return; }
      wss.handleUpgrade(req, socket, head, (ws) => {
        const params = url.searchParams;
        const fromWindow = params.get('host') === 'window';
        ws.send(JSON.stringify({ t: 'init', ...getSnapshot() }));

        const isWatcher = params.get('role') !== 'pet' || (!fromWindow && petWs && petWs.readyState === petWs.OPEN && petIsWindow);
        if (!isWatcher) {
          if (petWs) petWs.close(4000, 'replaced');
          petWs = ws;
          petIsWindow = fromWindow;
          log.info(`✨ 桌宠页面已连接 (${fromWindow ? 'Electron 透明穿透窗口' : '浏览器预览标签页'})`);

          ws.on('message', (data, isBinary) => {
            if (petWs !== ws) return;
            if (isBinary) {
              const buf = data as Buffer;
              if (buf.length % 2 === 0) {
                const frame = new Int16Array(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.length));
                if (asrState.listening && asrState.openSentence) {
                  asrState.openSentence.write(frame);
                } else {
                  // 热麦常驻时保留最近 7 帧 (~400ms) 前置环形缓冲，按下快捷键瞬间零延迟衔接
                  asrState.preRollFrames.push(frame);
                  if (asrState.preRollFrames.length > 7) asrState.preRollFrames.shift();
                }
              }
              return;
            }
            const msg = parseJson(data.toString());
            if (msg) handlePetWsMessage(msg);
          });
          ws.on('close', () => {
            if (petWs === ws) {
              petWs = null;
              petIsWindow = false;
              log.info('桌宠窗口连接已断开');
            }
          });
        } else {
          if (params.get('role') === 'pet') ws.send(JSON.stringify({ t: 'watching' }));
          dressers.add(ws);
          ws.on('message', (data, isBinary) => {
            if (isBinary) return;
            const msg = parseJson(data.toString());
            if (msg && ['text', 'prefs', 'control'].includes(msg.t)) handlePetWsMessage(msg);
          });
          ws.on('close', () => dressers.delete(ws));
        }
      });
    });

    server.on('error', reject);
    server.listen(PORT, '0.0.0.0', () => {
      resolve(PORT);
    });
  });
}

function applyClientPrefs(msg: Record<string, any>) {
  cfg.prefs ??= {};
  for (const k of ['roam', 'sound', 'theme', 'scale', 'bubbleSize', 'fontSize', 'bubbleSpeed', 'proactivePeek', 'perchWindows', 'hideFromCapture', 'micDevice', 'passWhenFullscreen', 'hideWhenFullscreen']) {
    if (k in msg) cfg.prefs[k] = msg[k];
  }
  if (typeof msg.micDevice === 'string') {
    log.info(`🎤 录音麦克风设备已切换为: ${msg.micDevice || '自动选择物理麦克风 (避开 Oopz/虚拟声卡)'}`);
  }
  if (typeof msg.proactivePeek === 'boolean') {
    cfg.proactive ??= {};
    cfg.proactive.enabled = msg.proactivePeek;
    log.info(`👀 主动看屏吐槽总开关已切换为: ${msg.proactivePeek ? '开启 (含游戏局内)' : '关闭'}`);
  }
  if (typeof msg.allowInGame === 'boolean') {
    cfg.proactive ??= {};
    cfg.proactive.allowInGame = msg.allowInGame;
    log.info(`🎮 游戏局内实时吐槽开关已切换为: ${msg.allowInGame ? '允许' : '免打扰'}`);
  }
  if (typeof msg.hideFromCapture === 'boolean') {
    log.info(`📸 截图隐身已切换为: ${msg.hideFromCapture ? '开启（截图不可见）' : '关闭（允许截图软件拍到弥酱）'}`);
  }
  if (typeof msg.perchWindows === 'boolean') {
    log.info(`🪟 蹲窗口顶边与自主爬窗已切换为: ${msg.perchWindows ? '开启' : '关闭'}`);
  }
  cfg.asr ??= {};
  let hotkeysChanged = false;
  if (typeof msg.talkHotkey === 'string' && msg.talkHotkey.trim() && parseHotkey(msg.talkHotkey.trim())) {
    cfg.asr.hotkey = msg.talkHotkey.trim();
    hotkeysChanged = true;
  }
  if (typeof msg.talkMode === 'string' && ['auto', 'toggle', 'hold'].includes(msg.talkMode)) {
    cfg.asr.mode = msg.talkMode;
    hotkeysChanged = true;
  }
  if (typeof msg.typeHotkey === 'string' && msg.typeHotkey.trim() && parseHotkey(msg.typeHotkey.trim())) {
    cfg.asr.typeHotkey = msg.typeHotkey.trim();
    hotkeysChanged = true;
  }
  if (typeof msg.typeKeyEnabled === 'boolean') {
    cfg.asr.typeKeyEnabled = msg.typeKeyEnabled;
    hotkeysChanged = true;
  }
  if (typeof msg.asrEngine === 'string' && ['sensevoice', 'system'].includes(msg.asrEngine)) {
    cfg.asr.engine = msg.asrEngine;
    log.info(`🧠 语音识别引擎已切换为: ${msg.asrEngine === 'sensevoice' ? 'SenseVoiceSmall (阿里 FunASR 高精度本地模型)' : 'Windows SAPI (系统内置)'}`);
    void startOrSwitchAsrEngine();
  }
  if (hotkeysChanged) {
    void rebindHotkeys();
  }
  if (typeof msg.mic === 'boolean') {
    cfg.asr.enabled = msg.mic;
    if (msg.mic && asrState.recognizer && !asrState.recognizer.ready) {
      void asrState.recognizer.start().then(() => {
        asrState.ready = !!asrState.recognizer?.ready;
        broadcastPrefs(getSnapshot());
      });
    }
  }
}

function handlePetWsMessage(msg: Record<string, any>) {
  switch (msg.t) {
    case 'text': {
      const text = String(msg.text || '').trim();
      const images = Array.isArray(msg.images) ? msg.images : [];
      if (!text && !images.length) return;
      log.info(`💬 收到桌宠打字框输入: "${text}"${images.length ? ` [附图×${images.length}]` : ''}`);
      void pushEventToBrain({
        event_type: 'typed',
        text,
        images,
        must_reply: true,
      });
      break;
    }
    case 'answer': {
      const askId = String(msg.askId || '');
      const text = typeof msg.text === 'string'
        ? `主人填写了回答：「${msg.text}」`
        : typeof msg.index === 'number'
          ? `主人选择了第 ${msg.index + 1} 项`
          : '主人关掉了提问气泡没有回答';
      log.info(`🗳️ 收到提问气泡回答 (${askId}): ${text}`);
      void pushEventToBrain({
        event_type: 'answer',
        ask_id: askId,
        index: msg.index,
        answer_text: msg.text,
        dismissed: !!msg.dismissed,
        text,
        must_reply: !msg.dismissed,
      });
      break;
    }
    case 'confirmed': {
      const id = String(msg.id || '');
      const pending = pendingConfirms.get(id);
      if (pending) {
        clearTimeout(pending.timer);
        pendingConfirms.delete(id);
        pending.resolve(typeof msg.index === 'number' ? msg.index : null);
      }
      break;
    }
    case 'touch':
      handlePetTouch(msg);
      break;
    case 'position':
      if (typeof msg.x === 'number') {
        startX = msg.x;
        cfg.startX = msg.x;
        if (typeof msg.y === 'number') { startY = msg.y; cfg.startY = msg.y; }
        saveConfig();
      }
      break;
    case 'prefs':
      applyClientPrefs(msg);
      saveConfig();
      broadcastPrefs(getSnapshot());
      break;
    case 'devices':
      if (Array.isArray(msg.list)) {
        asrState.devices = msg.list
          .filter((d: any) => d && typeof d.id === 'string')
          .map((d: any) => ({ id: String(d.id), label: String(d.label || d.id) }));
        broadcastPrefs(getSnapshot());
      }
      break;
    case 'mic':
      if (msg.state === 'on') {
        const label = typeof msg.detail === 'string' ? msg.detail : '默认麦克风';
        if (asrState.activeMicLabel !== label) {
          asrState.activeMicLabel = label;
          log.info(`🎤 麦克风热流已就绪: ${label}`);
          broadcastPrefs(getSnapshot());
        }
      } else if (msg.state === 'denied' || msg.state === 'error') {
        log.warn(`🎤 麦克风打开失败 (${msg.state}): ${msg.detail || ''}`);
      }
      break;
    case 'commit':
      if (asrState.listening) {
        void finishVoiceHold();
      }
      break;
    case 'cancel':
      if (asrState.listening || asrState.openSentence) {
        const open = asrState.openSentence;
        asrState.openSentence = null;
        asrState.listening = false;
        asrState.finishing = false;
        asrState.latched = false;
        try { open?.cancel?.(); } catch {}
        sendToPet({ t: 'talk-key', down: false });
        sendToPet({ t: 'listen', phase: 'none' });
        log.info('🛑 已取消当前语音录制并复位识别器流');
      }
      break;
  }
}

// =========================================================================
// 9. 反向长轮询命令桥 (穿透 Windows 防火墙接收远端 YOUR_EDGE_NODE_IP 的 pet_say / CUA 指令)
// =========================================================================
function buildMcpStateSnapshot() {
  const pack = currentPack();
  return {
    ok: true,
    petConnected: !!petWs && petWs.readyState === petWs.OPEN,
    petIsWindow,
    figure: pack.id,
    figureName: pack.manifest.name?.zh || pack.id,
    prefs: cfg.prefs,
    proactive: {
      ...cfg.proactive,
      enabled: cfg.prefs?.proactivePeek !== false && cfg.proactive?.enabled !== false,
      inGame: watcherState.inGame,
      foregroundApp: watcherState.lastApp,
      foregroundTitle: watcherState.lastTitle,
      lastPeekAt: watcherState.lastPeekAt,
    },
    cua: {
      ...cfg.cua,
      grantedRemainingSec: Math.max(0, Math.round((cuaEngine.grantedUntil - Date.now()) / 1000)),
    },
    asr: {
      ...cfg.asr,
      ready: asrState.ready,
      detail: asrState.detail,
    },
    screen: cuaEngine.latestScreen,
  };
}

function startBrainReversePoller(localPort: number) {
  let polling = false;
  const pollOnce = async () => {
    if (polling) return;
    polling = true;
    try {
      const candidates = activeBrainUrl
        ? [activeBrainUrl, ...BRAIN_CANDIDATES.filter((u) => u !== activeBrainUrl)]
        : BRAIN_CANDIDATES;
      for (const base of candidates) {
        try {
          const ctrl = new AbortController();
          const timer = setTimeout(() => ctrl.abort(), 4500);
          const res = await fetch(`${base}/deskpet_poll`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json; charset=utf-8',
              'Authorization': `Bearer ${AUTH_TOKEN}`,
            },
            body: JSON.stringify({ state: buildMcpStateSnapshot() }),
            signal: ctrl.signal,
          });
          clearTimeout(timer);
          if (!res.ok) continue;
          if (activeBrainUrl !== base) {
            activeBrainUrl = base;
            log.info(`🔗 已建立弥酱大脑双向反向通道: ${base}`);
          }
          const data = (await res.json()) as { ok?: boolean; commands?: Array<{ cmd_id: string; method: string; path: string; payload?: any }> };
          const cmds = Array.isArray(data?.commands) ? data.commands : [];
          for (const cmd of cmds) {
            void (async () => {
              let result: any = { ok: false, error: 'local_exec_failed' };
              try {
                const localRes = await fetch(`http://127.0.0.1:${localPort}${cmd.path}`, {
                  method: cmd.method || 'POST',
                  headers: {
                    'Content-Type': 'application/json; charset=utf-8',
                    'Authorization': `Bearer ${AUTH_TOKEN}`,
                  },
                  ...(cmd.method?.toUpperCase() === 'GET' ? {} : { body: JSON.stringify(cmd.payload || {}) }),
                });
                result = await localRes.json();
              } catch (err) {
                result = { ok: false, error: (err as Error).message };
              }
              try {
                await fetch(`${base}/deskpet_result`, {
                  method: 'POST',
                  headers: {
                    'Content-Type': 'application/json; charset=utf-8',
                    'Authorization': `Bearer ${AUTH_TOKEN}`,
                  },
                  body: JSON.stringify({ cmd_id: cmd.cmd_id, result }),
                });
              } catch {}
            })();
          }
          break;
        } catch {
          // 尝试下一个候选
        }
      }
    } finally {
      polling = false;
      setTimeout(pollOnce, 350);
    }
  };
  setTimeout(pollOnce, 600);
}

function launchElectronWindow(port: number) {
  const windowUrl = `http://127.0.0.1:${port}/pet#key=${windowKey}`;
  const isWin = process.platform === 'win32';
  const localElectron = join(ROOT_DIR, 'node_modules', '.bin', isWin ? 'electron.cmd' : 'electron');
  const electronBin = existsSync(localElectron) ? localElectron : 'npx';
  const args = existsSync(localElectron)
    ? [HOST_MAIN_CJS, `--pet-url=${windowUrl}`, `--parent-pid=${process.pid}`]
    : ['electron', HOST_MAIN_CJS, `--pet-url=${windowUrl}`, `--parent-pid=${process.pid}`];

  log.info(`🚀 正在启动 Electron 透明穿透桌宠窗口: ${windowUrl}`);
  const child = spawn(electronBin, args, {
    cwd: ROOT_DIR,
    stdio: 'inherit',
    shell: isWin,
  });
  child.on('exit', (code) => {
    log.info(`桌宠窗口已关闭 (exit=${code})，守护宿主即将退出...`);
    cuaEngine.stop();
    process.exit(0);
  });
}

async function sendFile(res: ServerResponse, full: string, csp: string | null, frame = false) {
  try {
    const bytes = await readFile(full);
    res.writeHead(200, {
      'content-type': MIME[extname(full)] ?? 'application/octet-stream',
      'cache-control': 'no-cache',
      'content-security-policy': csp ?? PAGE_CSP,
      'x-content-type-options': 'nosniff',
      ...(frame ? { 'access-control-allow-origin': 'null' } : {}),
    });
    res.end(bytes);
  } catch {
    res.writeHead(404).end();
  }
}

function json(res: ServerResponse, status: number, body: unknown) {
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'access-control-allow-origin': '*',
  });
  res.end(JSON.stringify(body));
}

function parseJson(text: string): Record<string, any> | null {
  try {
    const v = JSON.parse(text);
    return v && typeof v === 'object' && !Array.isArray(v) ? v : null;
  } catch {
    return null;
  }
}

function readBody(req: IncomingMessage, maxBytes = 256 * 1024): Promise<string | null> {
  return new Promise((done) => {
    let size = 0;
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => {
      size += c.length;
      if (size > maxBytes) { req.destroy(); done(null); return; }
      chunks.push(c);
    });
    req.on('end', () => done(Buffer.concat(chunks).toString('utf8')));
    req.on('error', () => done(null));
  });
}

// 启动主流程
async function main() {
  console.log('=================================================================');
  console.log(' 🌟 星见弥 (Hoshimi Mi) 桌宠与反作弊安全屏幕感知/CUA 客户端 V1.0');
  console.log('=================================================================');
  const port = await startServer();
  log.info(`🌐 本地服务已启动: http://127.0.0.1:${port}/pet (装扮页: http://127.0.0.1:${port}/dress)`);
  await initHotkeysAndAsr();
  startProactiveWatcher();
  startBrainReversePoller(port);
  if (!process.argv.includes('--no-window')) {
    launchElectronWindow(port);
  }
}

void main();
