import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "playwright-report", "test-results"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Claiming work / finishing auth on mount legitimately sets state from async effects.
      "react-hooks/set-state-in-effect": "off",
      // Project convention: `const` arrow functions only, never `function` declarations.
      "func-style": ["error", "expression"],
      "prefer-arrow-callback": "error",
    },
  },
);
