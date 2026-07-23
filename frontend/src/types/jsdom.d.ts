declare module "jsdom" {
  export class JSDOM {
    constructor(html?: string, options?: { runScripts?: string; url?: string });
    readonly window: Window & typeof globalThis;
  }
}
