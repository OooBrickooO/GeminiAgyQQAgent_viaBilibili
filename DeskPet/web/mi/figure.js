/**
 * 星见弥 (弥酱) 桌宠形象包 (web/mi/figure.js)
 *
 * 基于 Coopanion `web/kit/body.js` 物理引擎构建：
 * - 使用 GeminiAvatar_no_bg.png (`avatar.png`) 作为核心透明立绘；
 * - 完整继承 kit 的走路、奔跑、跳跃、呼吸弹性形变、坐下、睡觉、鼠标拎起晃动、甩出撞击摔晕；
 * - 在角色头顶与脸颊动态叠加二次元矢量漫符（脸红晕染、异色瞳星芒、十字怒气青筋、水滴冷汗、感叹号、问号、摔晕旋转金星、倾听声波、思考气泡、流泪瀑布）；
 * - 支持 GPT-SoVITS-v5 真声播放时的实时语音响度弹跳 (`frame.talk`)。
 */
import { DROP, f } from '../kit/body.js';

const AURA_COLORS = {
  starlight: { primary: '#B592FF', secondary: '#FF7EB3', ink: '#7B5CE6' },
  sakura:    { primary: '#FF7EB3', secondary: '#FFB8D1', ink: '#E85C90' },
  golden:    { primary: '#FFB84D', secondary: '#B592FF', ink: '#D99226' },
};

const MI_CSS = `
.mi-blush { fill: #FF6B9D; }
.mi-blush-line { stroke: #FF4D88; stroke-width: 2.2; stroke-linecap: round; }
.mi-sweat { fill: #66BFFF; stroke: #2B8AE0; stroke-width: 2; }
.mi-tear { fill: #66BFFF; }
.mi-angry { stroke: #FF4757; fill: none; stroke-width: 6.5; stroke-linecap: round; stroke-linejoin: round; }
.mi-mark-stroke { stroke: var(--mi-ink, #7B5CE6); fill: none; stroke-linecap: round; stroke-linejoin: round; }
.mi-mark-fill { fill: var(--mi-ink, #7B5CE6); }
.mi-star-a { fill: var(--mi-primary, #B592FF); }
.mi-star-b { fill: var(--mi-secondary, #FF7EB3); }
.mi-gold { fill: #FFC44D; stroke: #E69500; stroke-width: 2; }
`;

function starPath(cx, cy, rOuter, rInner = rOuter * 0.36) {
  let d = '';
  for (let i = 0; i < 8; i++) {
    const a = (i * Math.PI) / 4 - Math.PI / 2;
    const r = i % 2 === 0 ? rOuter : rInner;
    d += `${i === 0 ? 'M' : 'L'}${f(cx + r * Math.cos(a))} ${f(cy + r * Math.sin(a))}`;
  }
  return d + 'Z';
}

function createMiFigure(avatarHref) {
  let scheme = 'starlight';
  let drawn = '';

  const colorsOf = () => AURA_COLORS[scheme] || AURA_COLORS.starlight;

  function renderFrame(fc, o) {
    const t = o.t || 0;
    const lx = (o.look?.[0] || 0) * 0.45;
    const ly = (o.look?.[1] || 0) * 0.45;
    const sw = o.swing || 0;
    const talk = o.talk || 0;
    const sit = o.sit || 0;
    const drowse = o.drowse || 0;
    const c = colorsOf();

    // 坐下/打盹/说话时的姿态微调
    const sitDrop = sit * 18 + drowse * 8;
    const talkBounce = -talk * 5.5 * (0.6 + 0.4 * Math.sin(t * 28));
    const talkScaleY = 1 + talk * 0.032 * Math.sin(t * 24);
    const talkScaleX = 1 - talk * 0.018 * Math.sin(t * 24);
    const sleepTilt = drowse * 12;
    const happyTilt = (o.face === 'happy' || o.face === 'wink' || o.face === 'love') ? Math.sin(t * 5.5) * 3.5 : 0;
    const smugTilt = (o.face === 'smug' || o.face === 'pout') ? -4.5 : 0;
    const confusedTilt = o.face === 'confused' ? 8.5 : 0;

    // 手势姿态增强 (wave / flap / bow)
    let gestureRot = 0, gestureY = 0;
    if (o.gesture) {
      const k = o.gesture.k;
      const env = Math.sin(Math.PI * k);
      if (o.gesture.kind === 'wave' || o.gesture.kind === 'flap') {
        gestureRot = Math.sin(k * Math.PI * 4) * 8 * env;
      } else if (o.gesture.kind === 'bow') {
        gestureRot = 18 * env;
        gestureY = 10 * env;
      }
    }

    const totalRot = sw * 0.45 + sleepTilt + happyTilt + smugTilt + confusedTilt + gestureRot;
    const totalY = (o.low || 0) + sitDrop + talkBounce + gestureY;

    let s = `<g style="--mi-primary:${c.primary};--mi-secondary:${c.secondary};--mi-ink:${c.ink}">`;
    s += `<g transform="translate(128 ${f(248 + totalY)}) rotate(${f(totalRot)}) scale(${f(talkScaleX * 1000) / 1000} ${f(talkScaleY * 1000) / 1000}) translate(-128 -248)">`;

    // 1. 核心弥酱立绘 (256x256 舞台内居中立于 y=252)
    s += `<image href="${avatarHref}" x="10" y="10" width="236" height="242" preserveAspectRatio="xMidYMax meet"/>`;

    // 2. 脸颊红晕 (害羞/开心/喜欢/慌张/嘟嘴)
    const blush = Math.max(fc.blush || 0, (o.face === 'love' || o.face === 'shy' || o.face === 'flustered') ? 0.95 : 0);
    if (blush > 0.05) {
      const op = f(Math.min(0.88, blush * 0.85));
      // 对应立绘左右脸颊位置 (约 x=96, 152, y=132)
      s += `<g opacity="${op}">`;
      s += `<ellipse class="mi-blush" cx="${f(94 + lx)}" cy="${f(131 + ly)}" rx="12" ry="6.5"/>`;
      s += `<ellipse class="mi-blush" cx="${f(154 + lx)}" cy="${f(131 + ly)}" rx="12" ry="6.5"/>`;
      if (blush > 0.55) {
        s += `<path class="mi-blush-line" d="M${f(90 + lx)} ${f(129 + ly)}L${f(94 + lx)} ${f(134 + ly)}M${f(96 + lx)} ${f(129 + ly)}L${f(100 + lx)} ${f(134 + ly)}"/>`;
        s += `<path class="mi-blush-line" d="M${f(150 + lx)} ${f(129 + ly)}L${f(154 + lx)} ${f(134 + ly)}M${f(156 + lx)} ${f(129 + ly)}L${f(160 + lx)} ${f(134 + ly)}"/>`;
      }
      s += `</g>`;
    }

    // 3. 大哭瀑布泪流 / 委屈泪珠
    if (fc.streams) {
      const w1 = 2 * Math.sin(t * 7), w2 = 2 * Math.sin(t * 7 + 1.5);
      // 异色瞳位置约 (105, 118) 与 (144, 115)
      s += `<path class="mi-tear" opacity="0.82" d="M${f(101 + lx)} 126Q${f(99 + lx + w1)} 148 ${f(102 + lx)} 168L${f(110 + lx)} 168Q${f(108 + lx + w1)} 148 ${f(109 + lx)} 126Z"/>`;
      s += `<path class="mi-tear" opacity="0.82" d="M${f(140 + lx)} 124Q${f(138 + lx + w2)} 146 ${f(141 + lx)} 166L${f(149 + lx)} 166Q${f(147 + lx + w2)} 146 ${f(148 + lx)} 124Z"/>`;
    }

    // 4. 闪耀星芒 (开心/期待/得意/眨眼/认真)
    if (fc.sparkle || o.face === 'happy' || o.face === 'wink' || o.face === 'smug' || o.face === 'determined') {
      const k1 = 0.75 + 0.35 * Math.sin(t * 7.5);
      const k2 = 0.75 + 0.35 * Math.sin(t * 7.5 + 2.1);
      s += `<path class="mi-star-a" d="${starPath(44, 58, 12 * k1)}"/>`;
      s += `<path class="mi-star-b" d="${starPath(210, 46, 10 * k2)}"/>`;
      if (fc.sparkle) {
        const k3 = 0.7 + 0.3 * Math.sin(t * 9 + 4);
        s += `<path class="mi-gold" stroke="none" d="${starPath(192, 82, 8 * k3)}"/>`;
      }
    }

    // 5. 十字怒气青筋 💢 (生气)
    if (fc.anger) {
      const k = 1 + 0.14 * Math.sin(t * 11);
      s += `<g class="mi-angry" transform="translate(200 44) scale(${f(k)})"><path d="M-13 -4Q-4 -4 -4 -13M4 -13Q4 -4 13 -4M13 4Q4 4 4 13M-4 13Q-4 4 -13 4"/></g>`;
    }

    // 6. 额角水滴冷汗 💧 (被拎起/慌张/担心/害怕/冲刺)
    if (fc.sweat) {
      const sy = 56 + 4 * Math.sin(t * 7.5);
      s += `<path class="mi-sweat" transform="translate(46 ${f(sy)}) scale(1.35)" d="${DROP}"/>`;
    }

    // 7. 惊讶感叹号 ! 与疑惑问号 ?
    if (fc.bang) {
      const bPop = 1 + 0.08 * Math.sin(t * 14);
      s += `<g transform="translate(212 36) scale(${f(bPop)})"><path class="mi-mark-stroke" stroke-width="10" d="M0 -18V3"/><circle class="mi-mark-fill" cx="0" cy="16" r="5.5"/></g>`;
    }
    if (fc.question) {
      const qBob = 3 * Math.sin(t * 6);
      s += `<g transform="translate(212 ${f(38 + qBob)})"><path class="mi-mark-stroke" stroke-width="8.5" d="M-10 -12Q-10 -24 0 -24Q11 -24 11 -13Q11 -5 0 -1V4"/><circle class="mi-mark-fill" cx="0" cy="16" r="5.2"/></g>`;
    }

    // 8. 摔晕旋转小金星 (dizzy)
    if (fc.orbit) {
      for (let i = 0; i < 3; i++) {
        const a = t * 3.6 + i * 2.094;
        const sn = Math.sin(a);
        const cx = 128 + 64 * Math.cos(a);
        const cy = 18 + 11 * sn;
        const r = sn < 0 ? 7 : 10;
        s += `<path class="mi-gold" opacity="${sn < 0 ? '0.6' : '1'}" d="${starPath(cx, cy, r)}"/>`;
      }
    }

    // 9. 倾听声波弧线 (按下 LeftAlt*2 说话键时)
    if (fc.listen) {
      for (let i = 0; i < 3; i++) {
        const p = (t * 0.95 + i / 3) % 1;
        const r = 46 - 28 * p;
        const a0 = -0.55, a1 = 0.55;
        s += `<path class="mi-mark-stroke" stroke-width="6.5" opacity="${f(Math.sin(Math.PI * p))}" d="M${f(198 + r * Math.cos(a0))} ${f(98 + r * Math.sin(a0))}A${f(r)} ${f(r)} 0 0 1 ${f(198 + r * Math.cos(a1))} ${f(98 + r * Math.sin(a1))}"/>`;
      }
    }

    // 10. 思考上浮气泡 (当状态气泡关闭时的本体思考动画)
    if (fc.think) {
      for (let i = 0; i < 3; i++) {
        const k = (t * 0.8 + i / 3) % 1;
        s += `<circle class="mi-mark-stroke" stroke-width="4.5" cx="${f(208 + 10 * i)}" cy="${f(44 - 20 * i - 6 * k)}" r="${4 + 2.8 * i}" opacity="${f(0.4 + 0.6 * Math.sin(Math.PI * k))}"/>`;
      }
    }

    s += `</g></g>`;
    return s;
  }

  return {
    draw(petG, face, frame) {
      const s = renderFrame(face, frame);
      if (s === drawn && petG.firstChild) return;
      petG.innerHTML = s;
      drawn = s;
    },
    setScheme(id) {
      if (id && id in AURA_COLORS) scheme = id;
    },
    get colors() {
      return { z: colorsOf().primary };
    },
    get anchors() {
      return {
        gaze: [128, 116],
        tear: [144, 126],
        tears: [[105, 126], [144, 124]],
        z: [192, 34],
        hearts: [76, 180, 28],
        bubble: [128, 8],
      };
    },
    get extent() {
      return [16, 10, 240, 256];
    },
    get hits() {
      return [[128, 134, 106]];
    },
  };
}

export async function createMiBody(base, { scheme, kit, loadImage, asset, host }) {
  const avatarUrl = asset('avatar.png').href;
  await loadImage(avatarUrl);
  const fig = createMiFigure(avatarUrl);
  if (scheme) fig.setScheme(scheme);
  return kit.createBody(host, {
    figure: fig,
    css: MI_CSS,
    perch: true,
  });
}
