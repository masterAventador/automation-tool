import type {
  AccountDevice,
  AccountSessionGateway,
  AccountSessionSnapshot,
} from "../features/account-session/account-session-gateway";

/**
 * A signed-in product account, so the harness can render the layout a customer
 * Demo build actually shows.
 *
 * Without this the harness mounted `WorkbenchShell` on its own, while the
 * shipped customer Demo profile wraps it in `AccountSessionGate` — which adds a
 * ~68px account bar above the shell. Every layout assertion therefore measured
 * a page 68px shorter than the one the customer opens, and the whole document
 * scrolled on their machine while `e2e/shell-layout.spec.ts` stayed green.
 *
 * The stand-in answers only what the bar itself needs. It is not a substitute
 * for the real account acceptance (U9-xx), which runs against a real Control
 * Plane in a real App — this exists so the *layout* can be measured on the same
 * shape the customer gets.
 */
const HARNESS_ACCOUNT: AccountSessionSnapshot = {
  state: "authenticated",
  account: {
    userId: "3f3e6e5c-8a2b-4f2d-9c1a-0d5b7e6a4c21",
    loginName: "harness.demo",
    status: "active",
  },
};

const SIGNED_OUT: AccountSessionSnapshot = { state: "unauthenticated", account: null };

export class TestHarnessAccountSession implements AccountSessionGateway {
  async restoreSession(): Promise<AccountSessionSnapshot> {
    return HARNESS_ACCOUNT;
  }

  async login(): Promise<AccountSessionSnapshot> {
    return HARNESS_ACCOUNT;
  }

  async recoverPassword(): Promise<AccountSessionSnapshot> {
    return HARNESS_ACCOUNT;
  }

  async changePassword(): Promise<AccountSessionSnapshot> {
    return HARNESS_ACCOUNT;
  }

  async logout(): Promise<AccountSessionSnapshot> {
    return SIGNED_OUT;
  }

  async listDevices(): Promise<readonly AccountDevice[]> {
    return [];
  }

  async revokeDevice(): Promise<AccountDevice> {
    throw new Error("the harness account stand-in revokes nothing");
  }
}
