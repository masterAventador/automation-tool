import "@wdio/tauri-plugin";
import { runTaskProjectionAcceptance } from "./test-task-projection-acceptance";
import "./main";

declare global {
  interface Window {
    __automationToolTaskProjectionAcceptance?: typeof runTaskProjectionAcceptance;
  }
}

Object.defineProperty(window, "__automationToolTaskProjectionAcceptance", {
  configurable: false,
  enumerable: false,
  writable: false,
  value: runTaskProjectionAcceptance,
});
