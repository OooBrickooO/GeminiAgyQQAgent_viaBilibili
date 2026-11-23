/**
 * The pet page's microphone: 16 kHz mono through web/mic-worklet.js while the World asks for it.
 *
 * Smart physical microphone selection:
 * Voice chat tools like Oopz, Voicemeeter, or Virtual Audio Cable often register a virtual microphone
 * as Windows' default/communications device. When Oopz is closed, its virtual device produces silence
 * or fails. When `prefs.micDevice` is '' (Auto), we automatically prefer a real physical microphone
 * over virtual cables (`Oopz`, `Virtual`, `CABLE`, `Stereo Mix`, `立体声混音`), and we also support
 * manual device selection from Settings (`/dress`).
 */
const VIRTUAL_MIC_RE = /(oopz|virtual|cable|stereo mix|立体声混音|voicemeeter|steam streaming|what u hear|loopback|aux)/i;

export function createMic({ prefs, send, sendAudio }) {
  const media = navigator.mediaDevices;
  /** null, `{ starting }` while opening, or the open capture. */
  let mic = null;
  let retry = false;

  /**
   * Reports the inputs to the World and returns the capture's target: `deviceId` is the chosen device,
   * or '' for the system default, and `key` changes when the input behind the target changes.
   */
  async function reportDevices() {
    try {
      const inputs = (await media.enumerateDevices()).filter((d) => d.kind === 'audioinput');
      // before microphone access is granted the entries have an empty deviceId
      const list = inputs.filter((d) => d.deviceId && d.deviceId !== 'default' && d.deviceId !== 'communications');
      send({ t: 'devices', list: list.map((d) => ({ id: d.deviceId, label: d.label || `麦克风 (${d.deviceId.slice(0, 6)})` })) });
      if (!inputs.some((d) => d.deviceId)) return undefined;

      // 1. User explicitly chose a microphone device in Settings
      if (prefs.micDevice && list.some((d) => d.deviceId === prefs.micDevice)) {
        const picked = list.find((d) => d.deviceId === prefs.micDevice);
        return { deviceId: picked.deviceId, key: picked.deviceId, label: picked.label };
      }

      // 2. Auto mode: check what Windows' 'default' entry points to
      const defEntry = inputs.find((d) => d.deviceId === 'default') ?? inputs[0];
      const realMics = list.filter((d) => !VIRTUAL_MIC_RE.test(d.label || ''));

      // If 'default' is a virtual/Oopz/Stereo Mix device (or if we have real physical mics and want to avoid
      // Chromium opening a dead Windows 'communications' Oopz device when deviceId is empty),
      // pick the physical microphone that matches 'default' (if default is real) or the first real physical mic!
      if (realMics.length > 0) {
        // Check if 'default' label mentions one of the real mics (e.g. "默认 - 麦克风 (Realtek...)")
        const defLabel = (defEntry?.label || '').replace(/^(default|默认)\s*[-—:]\s*/i, '').trim();
        const matchedReal = defLabel && !VIRTUAL_MIC_RE.test(defLabel)
          ? realMics.find((d) => d.label === defLabel || defLabel.includes(d.label) || d.label.includes(defLabel))
          : null;
        const chosen = matchedReal ?? realMics[0];
        return { deviceId: chosen.deviceId, key: chosen.deviceId, label: chosen.label };
      }

      return { deviceId: '', key: JSON.stringify([defEntry.deviceId, defEntry.groupId, defEntry.label]), label: defEntry.label };
    } catch (err) {
      console.error(err);
      return undefined;
    }
  }

  async function start() {
    if (mic) return;
    retry = false;
    const opening = mic = { starting: true };
    let stream = null, ctx = null;
    try {
      const target = await reportDevices();
      if (mic !== opening) return;
      // AGC can change the system microphone volume on Windows.
      const audio = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false };
      const deviceId = target ? target.deviceId : prefs.micDevice;
      if (deviceId) audio.deviceId = { exact: deviceId };
      try {
        stream = await media.getUserMedia({ audio });
      } catch (firstErr) {
        // If the exact deviceId failed (e.g. unplugged), fallback to any available real mic or default
        if (deviceId) {
          delete audio.deviceId;
          stream = await media.getUserMedia({ audio });
        } else {
          throw firstErr;
        }
      }
      ctx = new AudioContext({ sampleRate: 16000 });
      if (ctx.state === 'suspended') await ctx.resume();
      await ctx.audioWorklet.addModule('/web/mic-worklet.js');
      if (mic !== opening) { stream.getTracks().forEach((t) => t.stop()); ctx.close(); return; }
      const node = new AudioWorkletNode(ctx, 'pet-mic');
      const trackLabel = stream.getAudioTracks()[0]?.label || target?.label || null;
      const active = mic = { stream, ctx, node, deviceId, key: target?.key, label: trackLabel };
      node.port.onmessage = (e) => { if (mic === active) sendAudio(e.data); };
      ctx.createMediaStreamSource(stream).connect(node);
      for (const track of stream.getAudioTracks()) {
        track.addEventListener('ended', () => {
          if (mic !== active) return;
          stop();
          if (prefs.mic) void start();
        });
      }
      send({ t: 'mic', state: 'on', detail: trackLabel });
      void follow();
    } catch (err) {
      stream?.getTracks().forEach((t) => t.stop());
      ctx?.close();
      if (mic !== opening) return;
      mic = null;
      const denied = err?.name === 'NotAllowedError';
      retry = !denied;
      send({ t: 'mic', state: denied ? 'denied' : 'error', detail: String(err?.message || err) });
      if (!denied) void reportDevices();
    }
  }

  /** Reopens the capture when its target is no longer the input it opened on. */
  async function follow() {
    const current = mic;
    const target = await reportDevices();
    if (mic !== current || target === undefined) return;
    if (current.key === undefined && target.deviceId === current.deviceId) { current.key = target.key; return; }
    if (target.key !== current.key) {
      stop();
      void start();
    }
  }

  function stop() {
    const m = mic;
    mic = null;
    retry = false;
    if (!m || m.starting) return;
    m.stream.getTracks().forEach((t) => t.stop());
    m.ctx.close();
    send({ t: 'mic', state: 'off' });
  }

  media?.addEventListener('devicechange', () => {
    if (!mic) { if (prefs.mic && retry) void start(); }
    else if (!mic.starting) void follow();
  });

  return { start, stop };
}
