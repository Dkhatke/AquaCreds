# AquaCreds v2

Blue-carbon MRV + registry + marketplace (SIH25038 rebuild). Before doing anything, read
`docs/CONTEXT.md`: it holds the decisions, the status log, and open questions. The plan is in
`docs/IMPLEMENTATION_PLAN.md`.

Rules for this repo:
- `_legacy/` is the old SIH prototype: reference only, git-ignored, never commit it or port its ML/carbon math.
- **Never commit to `main`.** Work on a feature branch (`docs/…`, `feat/…`, `fix/…`, `chore/…`), push, and open a PR; the user merges.
- Run git commands inside this folder only (the user's home directory is also a git repo).
- Python 3.12 via `uv`; TypeScript via `pnpm`. Docker images must build for `linux/arm64` (Oracle free VM).
- Never fabricate metrics or fall back to fake tx hashes/IDs. Surface failures as errors.
- Every carbon figure must be traceable to inputs, a methodology profile, and model versions.
- Update the `docs/CONTEXT.md` status log at the end of each working session.
