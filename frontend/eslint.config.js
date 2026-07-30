import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "src/api/generated", "src-tauri/gen", "src-tauri/target"] },
  {
    ...js.configs.recommended,
    files: ["**/*.{js,mjs}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.node,
    },
  },
  {
    files: ["src-tauri/src/material_video_studio_init.js"],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    // A measuring probe is Node at the top level and a browser inside every
    // `page.evaluate` callback, so it needs both. Declared per directory rather
    // than per file: the alternative is that the next probe is written, lands
    // with seven `no-undef` errors, and whoever hits them reaches for an
    // eslint-disable comment instead — which turns the rule off for real code
    // as easily as for a callback body.
    files: ["scripts/**/*.mjs"],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
    },
  },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["error", { allowConstantExport: true }],
    },
  },
  {
    files: ["e2e-tauri/**/*.ts"],
    languageOptions: {
      globals: globals.mocha,
    },
  },
);
