import { z } from "zod";

const LOCAL_BASE_URL = "http://127.0.0.1:8765";
const HOSTNAME_PATTERN =
  /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

const rawProfileSchema = z.discriminatedUnion("profile", [
  z
    .object({
      profile: z.literal("local"),
      baseUrl: z.string().url(),
    })
    .strict(),
  z
    .object({
      profile: z.literal("demo"),
      baseUrl: z.string().url(),
    })
    .strict(),
]);

export type BaseUrlProfile = Readonly<
  | { profile: "local"; baseUrl: typeof LOCAL_BASE_URL }
  | { profile: "demo"; baseUrl: string }
>;

export interface BaseUrlProfilePolicy {
  allowedDemoHosts: readonly string[];
}

export class BaseUrlProfileConfigurationError extends Error {
  constructor() {
    super("BaseUrl profile is invalid");
    this.name = "BaseUrlProfileConfigurationError";
  }
}

function requireRootOrigin(url: URL): void {
  if (
    url.username !== "" ||
    url.password !== "" ||
    url.pathname !== "/" ||
    url.search !== "" ||
    url.hash !== ""
  ) {
    throw new Error("URL must be a credential-free root origin");
  }
}

function allowedDemoHosts(policy: BaseUrlProfilePolicy): ReadonlySet<string> {
  const normalized = policy.allowedDemoHosts.map((hostname) => hostname.toLowerCase());
  if (normalized.some((hostname) => !HOSTNAME_PATTERN.test(hostname))) {
    throw new Error("Allowed host is invalid");
  }
  return new Set(normalized);
}

export function parseBaseUrlProfile(
  input: unknown,
  policy: BaseUrlProfilePolicy = { allowedDemoHosts: [] },
): BaseUrlProfile {
  try {
    const raw = rawProfileSchema.parse(input);
    const url = new URL(raw.baseUrl);
    requireRootOrigin(url);

    if (raw.profile === "local") {
      if (url.origin !== LOCAL_BASE_URL) {
        throw new Error("Local origin is not the fixed loopback endpoint");
      }
      return Object.freeze({ profile: "local", baseUrl: LOCAL_BASE_URL });
    }

    if (
      url.protocol !== "https:" ||
      url.port !== "" ||
      !allowedDemoHosts(policy).has(url.hostname)
    ) {
      throw new Error("Demo origin is not allowed");
    }

    return Object.freeze({ profile: "demo", baseUrl: url.origin });
  } catch {
    throw new BaseUrlProfileConfigurationError();
  }
}

export const LOCAL_CONTROL_PLANE_PROFILE = parseBaseUrlProfile({
  profile: "local",
  baseUrl: LOCAL_BASE_URL,
});
