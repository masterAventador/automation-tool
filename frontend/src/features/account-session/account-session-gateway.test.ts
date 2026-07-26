import { describe, expect, it } from "vitest";

import {
  AccountSessionGatewayError,
  parseAccountSessionSnapshot,
} from "./account-session-gateway";

describe("product account Session gateway contract", () => {
  it("accepts only a secret-free authenticated account projection", () => {
    expect(
      parseAccountSessionSnapshot({
        state: "authenticated",
        account: {
          userId: "123e4567-e89b-42d3-a456-426614174000",
          loginName: "demo.operator",
          status: "active",
        },
      }),
    ).toEqual({
      state: "authenticated",
      account: {
        userId: "123e4567-e89b-42d3-a456-426614174000",
        loginName: "demo.operator",
        status: "active",
      },
    });

    for (const value of [
      {
        state: "authenticated",
        account: {
          userId: "123e4567-e89b-42d3-a456-426614174000",
          loginName: "demo.operator",
          status: "active",
        },
        accessToken: "atas1.private",
      },
      { state: "unauthenticated", account: null, refreshToken: "atrs1.private" },
      { state: "authenticated", account: null },
      { state: "unauthenticated", account: { loginName: "demo.operator" } },
    ]) {
      expect(() => parseAccountSessionSnapshot(value)).toThrow(AccountSessionGatewayError);
    }
  });

  it("accepts the deployment that issues no product accounts, and never with an account", () => {
    expect(parseAccountSessionSnapshot({ state: "not_required", account: null })).toEqual({
      state: "not_required",
      account: null,
    });

    // "This deployment has no product accounts" and "somebody is logged in"
    // must stay mutually exclusive, or the gate could open on a projection it
    // never authenticated.
    for (const value of [
      {
        state: "not_required",
        account: {
          userId: "123e4567-e89b-42d3-a456-426614174000",
          loginName: "demo.operator",
          status: "active",
        },
      },
      { state: "not_required" },
      { state: "not_required", account: null, accessToken: "atas1.private" },
    ]) {
      expect(() => parseAccountSessionSnapshot(value)).toThrow(AccountSessionGatewayError);
    }
  });
});
