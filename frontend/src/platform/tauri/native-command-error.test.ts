import { describe, expect, it } from "vitest";

import { nativeCommandErrorFields } from "./native-command-error";

describe("native command error wire shape", () => {
  it("reads the production wire, which now carries the readable message", () => {
    expect(
      nativeCommandErrorFields({
        code: "installation_access_denied",
        message: "native command error: installation_access_denied",
        retryable: false,
      }),
    ).toEqual({ code: "installation_access_denied", retryable: false });
  });

  it("still reads a native error that carries no message", () => {
    expect(nativeCommandErrorFields({ code: "timed_out", retryable: true })).toEqual({
      code: "timed_out",
      retryable: true,
    });
  });

  it("returns only the structured fields, so a native message can never be reflected", () => {
    const fields = nativeCommandErrorFields({
      code: "storage_unavailable",
      message: "password=private-native-secret",
      retryable: false,
    });

    expect(fields).toEqual({ code: "storage_unavailable", retryable: false });
    expect(JSON.stringify(fields)).not.toContain("private-native-secret");
  });

  it("refuses any key the boundary does not define", () => {
    expect(
      nativeCommandErrorFields({
        code: "authentication_rejected",
        retryable: false,
        apiKey: "sk-private-native-secret",
      }),
    ).toBeUndefined();
  });

  it("refuses wrong field types and non-objects", () => {
    for (const value of [
      { code: 7, retryable: false },
      { code: "timed_out", retryable: "false" },
      { code: "timed_out", message: 7, retryable: false },
      { code: "timed_out" },
      { retryable: false },
      new Error("password=private-native-secret"),
      "timed_out",
      null,
      undefined,
      [],
    ]) {
      expect(nativeCommandErrorFields(value)).toBeUndefined();
    }
  });
});
