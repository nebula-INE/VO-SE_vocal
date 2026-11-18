# Lint policy

## Enforced checks

- `npm run lint:web` runs ESLint with zero warnings on the Web code changed in the Desktop/Web parity work:
  - `assets/src/components/BatchLyricModal.tsx`
  - `assets/src/components/InspectorPanel.tsx`
  - `assets/src/utils/lyricTokens.js`
  - `tests/test_lyric_tokenization.mjs`
- `npm run lint:python` runs Ruff across `modules`, `tests`, and `main.py`.
- `npm run lint` runs both checks. CI executes them as separate jobs.

## Incremental expansion

A first repository-wide ESLint run surfaced 47 existing errors and 148 warnings in untouched Web/Node files. These include no-empty catch blocks, regular-expression control-character checks in path sanitization, existing TypeScript suppressions, and unused declarations.

Do not use broad automatic fixes across renderer, audio encoding, voicebank security, or export code merely to make the first lint run green. Expand ESLint file-by-file after reviewing each finding and adding regression coverage. New and modified Web files must remain at zero ESLint warnings/errors.

The existing baseline should be retired progressively. It is not considered lint-clean simply because the incremental check passes.
