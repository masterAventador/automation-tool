import { describe, expect, it } from "vitest";

import {
  BaseUrlProfileConfigurationError,
  LOCAL_CONTROL_PLANE_PROFILE,
  parseBaseUrlProfile,
} from "./base-url-profile";

const demoPolicy = { allowedDemoHosts: ["demo-api.example.com"] };

describe("BaseUrl Profile", () => {
  it("exports one canonical fixed local profile", () => {
    expect(LOCAL_CONTROL_PLANE_PROFILE).toEqual({
      profile: "local",
      baseUrl: "http://127.0.0.1:8765",
    });
    expect(Object.isFrozen(LOCAL_CONTROL_PLANE_PROFILE)).toBe(true);
  });

  it("accepts and canonicalizes an allowlisted HTTPS demo origin", () => {
    expect(
      parseBaseUrlProfile(
        { profile: "demo", baseUrl: "https://DEMO-API.EXAMPLE.COM/" },
        demoPolicy,
      ),
    ).toEqual({
      profile: "demo",
      baseUrl: "https://demo-api.example.com",
    });
  });

  it.each([
    { profile: "local", baseUrl: "http://localhost:8765" },
    { profile: "local", baseUrl: "http://127.0.0.1:8000" },
    { profile: "local", baseUrl: "https://127.0.0.1:8765" },
    { profile: "local", baseUrl: "http://127.0.0.1:8765/api" },
    { profile: "local", baseUrl: "http://operator:private@127.0.0.1:8765" },
    { profile: "demo", baseUrl: "http://demo-api.example.com" },
    { profile: "demo", baseUrl: "https://demo-api.example.com.evil.test" },
    { profile: "demo", baseUrl: "https://demo-api.example.com@evil.test" },
    { profile: "demo", baseUrl: "https://demo-api.example.com:8443" },
    { profile: "demo", baseUrl: "https://demo-api.example.com/api" },
    { profile: "demo", baseUrl: "https://demo-api.example.com?token=private" },
    { profile: "demo", baseUrl: "https://demo-api.example.com#private" },
    { profile: "production", baseUrl: "https://demo-api.example.com" },
    { profile: "demo", baseUrl: "https://demo-api.example.com", extra: true },
  ])("rejects an unsafe or unsupported profile: $baseUrl", (input) => {
    expect(() => parseBaseUrlProfile(input, demoPolicy)).toThrow(
      BaseUrlProfileConfigurationError,
    );
  });

  it("fails closed without reflecting rejected configuration", () => {
    const privateInput = {
      profile: "demo",
      baseUrl: "https://operator:private-password@demo-api.example.com",
    };

    let captured: unknown;
    try {
      parseBaseUrlProfile(privateInput, demoPolicy);
    } catch (error) {
      captured = error;
    }

    expect(captured).toBeInstanceOf(BaseUrlProfileConfigurationError);
    expect(String(captured)).toBe("BaseUrlProfileConfigurationError: BaseUrl profile is invalid");
    expect(String(captured)).not.toContain("private-password");
    expect((captured as Error).cause).toBeUndefined();
  });
});
