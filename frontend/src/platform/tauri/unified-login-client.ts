import { invoke } from "@tauri-apps/api/core";
import { TauriAuthClient } from "@unified-login/tauri";

export function createUnifiedLoginClient(): TauriAuthClient {
  return new TauriAuthClient((command, arguments_) => invoke(command, arguments_));
}
