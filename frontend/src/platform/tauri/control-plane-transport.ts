import {
  ControlPlaneTransportError,
  type ControlPlaneHealth,
  type ControlPlaneTransport,
} from "../../api/control-plane/transport";

/**
 * Production composition seam. I2-09 will connect this narrow operation to the
 * Rust allowlisted network bridge; it intentionally exposes no URL or fetch API.
 */
export class TauriControlPlaneTransport implements ControlPlaneTransport {
  async checkHealth(): Promise<ControlPlaneHealth> {
    throw new ControlPlaneTransportError("transport_unavailable", true);
  }
}
