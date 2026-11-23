/** Computer use's part of the desktop-pet config, `worlds.desktop-pet.cua`: its defaults. */

/**
 * When the person is asked before the bot uses the computer, from strictest to loosest:
 * - ask-each-turn: every turn, before the first screenshot, window list or input;
 * - ask-before-acting: screenshots and the window list are free; every turn, before the first input;
 * - ask-once: screenshots and the window list are free; before an input, and a yes holds for `grantMinutes`;
 * - never-ask: never.
 */
export const PERMISSION_LEVELS = ['ask-each-turn', 'ask-before-acting', 'ask-once', 'never-ask'] as const;
export type PermissionLevel = (typeof PERMISSION_LEVELS)[number];

export interface CuaConfig {
  /** Off: no computer-use tools and no computer-use part in the environment prompt. */
  enabled: boolean;
  /** false: screenshots and the window list only; every input tool fails with that reason. */
  control: boolean;
  /** See PERMISSION_LEVELS. */
  permission: PermissionLevel;
  /** ask-once and the terminal question: how long a yes holds. */
  grantMinutes: number;
  /** On: typing and keys in a terminal window and Win+R are asked about only as `permission` says, like any other input. */
  skipTerminalAsk: boolean;
  screenshot: {
    maxWidth: number;
    maxHeight: number;
    /** JPEG quality, 1–100. */
    quality: number;
    /** Input tools attach a screenshot unless the call says otherwise. */
    afterAction: boolean;
    /** Wait after an action before taking its screenshot. */
    settleMs: number;
  };
  /** Before sending input, the user must have left mouse and keyboard alone this long. */
  userIdleMs: number;
  /** How long an input tool waits for that before giving up. */
  maxYieldWaitMs: number;
  /** Pause between 16-character chunks while typing. */
  typeChunkDelayMs: number;
}

export const CUA_DEFAULTS: CuaConfig = {
  enabled: true,
  control: true,
  permission: 'ask-once',
  grantMinutes: 30,
  skipTerminalAsk: false,
  screenshot: { maxWidth: 1280, maxHeight: 800, quality: 78, afterAction: true, settleMs: 500 },
  userIdleMs: 2000,
  maxYieldWaitMs: 15_000,
  typeChunkDelayMs: 20,
};
