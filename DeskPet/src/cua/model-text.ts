/**
 * What the bot reads from computer use (receipts and environment prompt values), in Chinese and in
 * English; `en: typeof zh` keeps the two tables' keys and signatures the same.
 */
import type { PermissionLevel } from './config.ts';
import type { EngineErrorCode } from './engine/fail.ts';
import type { YieldDiagnostics } from './engine-ipc.ts';

export type ModelLanguage = 'zh' | 'en';

type Button = 'left' | 'right' | 'middle';

const BUTTON_ZH: Record<Button, string> = { left: '左键', right: '右键', middle: '中键' };

const zh = {
  notRun: (tool: string, why: string) => `[${tool} 没执行] ${why}`,
  failed: (tool: string, why: string) => `[${tool} 失败] ${why}`,
  askAgain: '下一轮再用会重新询问。',
  interruptedBeforeAct: '收到打断时还没开始操作。',
  interruptedAsking: '收到打断时还在等使用者回答能不能用电脑。',
  askTimedOut: '问了使用者能不能用电脑,没有等到回应,这一轮不能用。',
  terminalRefused: '使用者这一轮没有允许在命令行窗口里输入或按键,也没有允许按 win+r。',
  refused: (level: PermissionLevel) => `使用者这一轮没有允许${level === 'ask-each-turn' ? '用电脑' : '动鼠标键盘'}。`,
  worldStopped: 'World 已停止',
  switchedOff: '电脑操作已关闭',
  /** `code` absent: a clean exit. */
  engineExited: (code?: number | null) => (code === undefined ? '引擎进程已退出' : `引擎进程退出(退出码 ${code})`),
  engineTimeout: '引擎没有在期限内应答',
  /** What the engine's error codes say; `args` as the engine sent them. */
  engineErrors: {
    macScreenPermission: () => '没有「屏幕录制」权限',
    macInputPermission: () => '没有「辅助功能」权限',
    macNoKey: (vk) => `Mac 键盘上没有这个键(虚拟键码 ${vk})`,
    x11Display: () => '连不上 X11 显示(DISPLAY 没有设置?)',
    linuxNoScreenshot: () => '截屏失败:X 服务器给不出屏幕画面',
    linuxPixelFormat: (bits) => `截屏失败:不支持的像素格式(${bits} 位)`,
    linuxNoKey: (vk) => `这个键盘布局里没有这个键(虚拟键码 ${vk})`,
    linuxNoXdotool: () => '打字需要 xdotool',
    winBitBlt: () => 'BitBlt 失败(可能被更高权限的窗口挡住)',
    winGetDIBits: (lines, height) => `GetDIBits 只取到 ${lines}/${height} 行`,
    winSendInput: (sent, total) => `SendInput 只送出 ${sent}/${total} 个事件(可能被更高权限的窗口挡住)`,
    winSetCursorPos: (x, y) => `SetCursorPos(${x}, ${y}) 失败(可能被更高权限的窗口挡住)`,
    layoutChanged: () => '显示器变了(接上、拔掉、挪动或改了缩放),这次没有操作;先重新截图,再按新截图的坐标操作',
    noDisplay: (n, total) => `没有显示器 ${n},现在有 ${total} 块`,
  } as Record<EngineErrorCode, (...args: Array<string | number | undefined>) => string>,

  screenshot: (lead: string, w: number, h: number, view: string, cursor: string, foreground: string | null, seen: boolean) =>
    `${lead}截图 ${w}×${h}(${view});鼠标${cursor};前台窗口「${foreground === null ? '无' : foreground || '标题不可用'}」。` + (seen ? '' : '\n当前模型不接收图片,只能读到这段文字。'),
  screenshotFallback: (w: number, h: number) => `屏幕截图 ${w}×${h}`,
  viewOne: (pw: number, ph: number) => `屏幕 ${pw}×${ph}`,
  viewOf: (n: number, total: number, pw: number, ph: number, primary: boolean, others: string) => `显示器 ${n}/${total},${pw}×${ph}${primary ? ',主显示器' : ''};另有显示器 ${others}`,
  displayBrief: (n: number, pw: number, ph: number, primary: boolean) => `${n}(${pw}×${ph}${primary ? ',主显示器' : ''})`,
  cursorAt: (p: string) => `在 ${p}`,
  cursorOn: (n: number) => `在显示器 ${n} 上`,
  cursorNowhere: '不在任何显示器上',
  displayBad: (n: unknown, total: number) => `没有显示器 ${String(n)};现在有 ${total} 块,编号 1–${total}。`,
  zoomed: (w: number, h: number, x: number, y: number, rw: number, rh: number) => `放大图 ${w}×${h}:截图里 (${x}, ${y}) 起 ${rw}×${rh} 的一块,从屏幕重新截取,不大于截图尺寸上限时不缩小。点击等操作仍用截图坐标。`,
  zoomFallback: (w: number, h: number) => `局部放大图 ${w}×${h}`,
  zoomBad: 'x、y 要在截图范围内,width、height 要是正数',
  zoomNoShot: '还没有截图:先用 pet_cua_screenshot 截一张,放大的区域用那张截图的坐标',
  listSep: '、',
  viewOnly: '这台电脑的设置只允许看,不允许操作鼠标键盘。',
  yielded: (seconds: number) => `等了 ${seconds} 秒,输入信号仍未满静止时长,本次操作未执行。`,
  yieldDetails: (activity: YieldDiagnostics, foreground: string | null, app: string | null) => {
    const signals = activity.blockedBy.map((s) => s === 'systemInput' ? '系统最后输入时间' : '指针位移').join('、');
    const source = activity.blockedBy.includes('systemInput') && activity.pointerMovement.count === 0
      ? '未检测到近期指针位移,具体输入设备未知,可能是键盘、滚轮或其他输入设备。' : '';
    return `阻挡信号:${signals}。${source}`
      + `让位检查命中:系统输入 ${activity.systemInput.count} 次(最后 tick ${activity.systemInput.lastTick ?? '无'}),指针位移 ${activity.pointerMovement.count} 次(最后 tick ${activity.pointerMovement.lastTick ?? '无'});次数是采样检查次数。`
      + `前台窗口「${foreground === null ? '无' : foreground || '标题不可用'}」,程序「${app ?? '未知'}」。`;
  },
  cancelledWaiting: (seconds: string) => `等用户停手 ${seconds} 秒时收到打断,没有发出输入。`,
  waitedFirst: (seconds: string) => `(先等用户停手 ${seconds} 秒)`,
  after: (line: string, cursor: string, foreground: string | null) => `${line} 鼠标${cursor};前台窗口「${foreground === null ? '无' : foreground || '标题不可用'}」。`,
  noForeground: '现在没有窗口在接收键盘输入;要打字先截图看看。',
  noShotInterrupted: '收到打断,没有截图。',

  notNumbers: 'x、y 要是数字',
  offShot: (x: number, y: number, maxX: number, maxY: number) => `(${x}, ${y}) 不在截图范围内(0–${maxX}, 0–${maxY})`,
  clicked: (x: unknown, y: unknown, button: Button, count: 1 | 2 | 3) =>
    `已在 (${x}, ${y}) ${BUTTON_ZH[button]}${{ 1: '单击', 2: '双击', 3: '三击' }[count]}。`,
  moved: (x: unknown, y: unknown) => `鼠标已移到 (${x}, ${y})。`,
  dragStart: (why: string) => `起点${why}`,
  dragEnd: (why: string) => `终点${why}`,
  dragged: (from: unknown[], to: unknown[]) => `已从 (${from[0]}, ${from[1]}) 拖到 (${to[0]}, ${to[1]})。`,
  mouseDown: (x: unknown, y: unknown, button: Button) => `已在 (${x}, ${y}) 按下${BUTTON_ZH[button]},没有松开。`,
  mouseUp: (button: Button) => `已松开${BUTTON_ZH[button]}。`,
  scrollNone: 'down 和 right 都是 0。',
  scrolled: (x: unknown, y: unknown, down: number, right: number) => {
    const parts = [down ? `${down > 0 ? '向下' : '向上'} ${Math.abs(down)} 格` : '', right ? `${right > 0 ? '向右' : '向左'} ${Math.abs(right)} 格` : ''].filter(Boolean);
    return `已在 (${x}, ${y}) 滚动${parts.join('、')}。`;
  },
  typeEmpty: 'text 是空的。',
  typedPart: (typed: number, total: number, by: 'cancel' | 'user') => `只输入了 ${typed}/${total} 个字符:${by === 'cancel' ? '收到打断' : '用户开始操作'},停了下来。`,
  typed: (total: number) => `已输入 ${total} 个字符。`,
  keyBad: (why: string) => `${why}。`,
  keyUnknown: (name: string) => `不认识的键「${name}」`,
  keyNone: '没有给出按键',
  pressed: (spec: string) => `已按 ${spec}。`,
  holdOneChord: 'hold 只配一个键或一个组合键(用 + 连接),不能用空格分成几步。',
  held: (spec: string, seconds: number) => `已按住 ${spec} ${seconds} 秒后松开。`,
  heldPart: (spec: string, held: string, seconds: number) => `按住 ${spec} ${held}/${seconds} 秒时收到打断,已松开。`,
  minimized: '最小化',
  offScreen: '不在任何显示器上',
  onDisplay: (n: number) => `在显示器 ${n}`,
  windowLine: (handle: string, foreground: boolean, title: string, where: string) => `- ${handle}${foreground ? ' [前台]' : ''} 「${title}」 ${where}`,
  windows: (n: number, lines: string[]) => `可见窗口 ${n} 个(最近一张截图那块显示器上的窗口位置用截图坐标;前面的在上层):\n${lines.join('\n')}`,
  focusEmpty: 'window 是空的。',
  focusMissing: (key: string) => `没有标题包含「${key}」的可见窗口。`,
  focused: (title: string) => `已把「${title}」切到前台。`,
  focusedElsewhere: (n: number) => `它在显示器 ${n},要看它就截那块显示器。`,
  focusRefused: (title: string, foreground: string | null) => `尝试切换到「${title}」,系统没有让它到前台;现在前台是「${foreground === null ? '无' : foreground || '标题不可用'}」。`,
  waitInterrupted: (waited: string, seconds: number) => `等了 ${waited}/${seconds} 秒时收到打断,没有截图。`,
  waited: (seconds: number) => `等了 ${seconds} 秒。`,
  waitNoLook: (seconds: number) => `等了 ${seconds} 秒。这一轮使用者还没允许看屏幕,所以没有截图;要看就用 pet_cua_screenshot,会先问使用者。`,

  displays: (n: number, list: string) => `这台电脑接了 ${n} 块显示器:${list}。截图一次截一块:\`pet_cua_screenshot\` 不带 display 时截上次看的那块,第一次截前台窗口所在的那块;要看别的显示器就带上编号。新打开的窗口可能出现在另一块显示器上,\`pet_cua_windows\` 会写明每个窗口在哪块。`,
  keysMac: '这是 Mac:复制粘贴、全选、保存用 cmd(cmd+c、cmd+v、cmd+a、cmd+s),切换应用用 cmd+tab。',
  keysOther: '复制粘贴、全选、保存用 ctrl(ctrl+c、ctrl+v、ctrl+a、ctrl+s),切换窗口用 alt+tab。',
  control: (allowed: boolean): string => (allowed ? '允许操作鼠标和键盘' : '只允许截图和列窗口,不能操作鼠标键盘'),
  permission: (level: PermissionLevel, grantMinutes: number) => ({
    'ask-each-turn': '每一轮第一次截图或操作之前,使用者会被问一次能不能用电脑。使用者没同意,这一轮的电脑操作工具都不执行;不要换别的工具绕过去,等使用者开口。',
    'ask-before-acting': '截图和列窗口不用先问。每一轮第一次动鼠标或键盘之前,使用者会被问一次;使用者没同意,这一轮的输入工具都不执行,截图照常;不要换别的工具绕过去,等使用者开口。',
    'ask-once': `截图和列窗口不用先问。动鼠标或键盘之前会问使用者一次,同意后 ${grantMinutes} 分钟内不再问;使用者没同意,这一轮的输入工具都不执行,截图照常;不要换别的工具绕过去,等使用者开口。`,
    'never-ask': '看屏幕和动鼠标键盘都不用先问使用者,工具直接执行。',
  })[level],
  terminal: (grantMinutes: number) => `在命令行窗口里打字或按键,以及按 win+r 打开运行框之前,不管上面的询问设置,都会单独问使用者一次,同意后 ${grantMinutes} 分钟内不再问;使用者没同意,这一轮这些步骤都不执行。`,
  terminalSkipped: '在命令行窗口里打字或按键、按 win+r 打开运行框,和别的输入一样按上面的询问设置,没有单独的询问。',
};

const en: typeof zh = { ...zh };

export type ModelText = typeof zh;

export const MODEL_TEXT: Record<ModelLanguage, ModelText> = { zh, en };
