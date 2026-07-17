import {
  ControlPlaneTransportError,
  type ControlPlaneHealth,
  type ControlPlaneRequestOptions,
  type ControlPlaneTransport,
} from "./transport";

export interface TestHarnessControlPlaneHandlers {
  readonly checkHealth?: (
    options: ControlPlaneRequestOptions,
  ) => Promise<ControlPlaneHealth>;
}

class TestHarnessControlPlaneTransport implements ControlPlaneTransport {
  constructor(private readonly handlers: TestHarnessControlPlaneHandlers) {}

  async checkHealth(
    options: ControlPlaneRequestOptions = {},
  ): Promise<ControlPlaneHealth> {
    const handler = this.handlers.checkHealth;
    if (handler === undefined) {
      throw new ControlPlaneTransportError("operation_unavailable", false);
    }
    return handler(options);
  }
}

export function createTestHarnessControlPlaneTransport(
  handlers: TestHarnessControlPlaneHandlers,
): ControlPlaneTransport {
  return new TestHarnessControlPlaneTransport(handlers);
}
