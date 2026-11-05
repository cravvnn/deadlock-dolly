# Development model checks

Vendored MIT runner from tla-skill, pinned to tla-ts0.0.8. See LICENSE.txt and
package-lock.json. Requires Node20/npm for development only; no app dependency.
Run `npm ci --prefix tools/tla --ignore-scripts`, then `python tools/check_tla.py`.
Models live beside the source; SPECS.md records scope and limitations. Failed
models, stale mutants, surviving mutants or missing FollowSelection.cfg fail the
build before native compilation. This runner does not extract Python semantics.
