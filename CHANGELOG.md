# Changelog

## 0.1.3 - 2026-11-20

- Align `allowedTools` across `SKILL.md`, the Qwen Code host guide, the manifest marker, and contract tests using Qwen Code's array syntax.
- Teach the package verifier to inspect block-style `allowedTools`/`allowed-tools` lists so forbidden tool pre-approvals cannot be missed.
- Refresh the downloadable release archive so consumers receive the corrected package.

## 0.1.2 - 2026-11-20

- Add the English README and language switch.
- Clarify the Apache-2.0 ecosystem skill license boundary and the related main project's noncommercial status.
- Simplify feedback to the problem encountered, the model used, and the shell/host used.
- Refresh the downloadable release archive so it matches the current documentation.

## 0.1.1 - 2026-11-18

- Standardize host/model-specific repository metadata, README labels, and release packaging.
- Publish downloadable archives with SHA-256 checksums.
- Separate deterministic package and contract checks from model-behavior evidence in the README and model-eval record format.

## 0.1.0 - 2026-11-18

- Split `qwen-grounded-audit` out of the combined `grounded-audit` package.
- Keep the evidence-first audit method, read-only default, memory recorder, package verifier and Apache-2.0 licensing.
- Specialize the skill for Qwen Code and the Qwen（千问） model profile.
