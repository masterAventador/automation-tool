/**
 * The shape every Tauri command error arrives in, in one place.
 *
 * A command that fails rejects `invoke()` with the JSON serialization of its Rust
 * error type. That wire is `{ code, message, retryable? }`: `code` and `retryable`
 * are what the gateways branch on, and `message` exists because JavaScript reads a
 * rejection as `error.message` first and `String(error)` second — a plain object
 * answers neither, so an error without it reads as `[object Object]` in the console,
 * in an uncaught rejection and in the desktop E2E runner.
 *
 * Six gateways used to spell this shape out themselves, three of them by requiring
 * exactly the two keys `code` and `retryable`. Adding `message` on the Rust side
 * therefore silently downgraded every one of their errors to the opaque fallback.
 * Keeping the shape here means the next field is decided once.
 *
 * `message` is validated but never returned. It is diagnostic text produced by the
 * native side, and the rule that no native wording reaches rendered copy predates
 * it: callers get the closed-set `code` and map it to their own user-facing text.
 */

const WIRE_KEYS: ReadonlySet<string> = new Set(["code", "message", "retryable"]);

export interface NativeCommandErrorFields {
  readonly code: string;
  readonly retryable: boolean;
}

/**
 * The structured fields of a native command error, or `undefined` when the value
 * is anything else — a transport failure, a thrown `Error`, or a payload carrying
 * a key this boundary does not define.
 */
export function nativeCommandErrorFields(value: unknown): NativeCommandErrorFields | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  if (!Object.keys(record).every((key) => WIRE_KEYS.has(key))) {
    return undefined;
  }
  if (record.message !== undefined && typeof record.message !== "string") {
    return undefined;
  }
  if (typeof record.code !== "string" || typeof record.retryable !== "boolean") {
    return undefined;
  }
  return { code: record.code, retryable: record.retryable };
}
