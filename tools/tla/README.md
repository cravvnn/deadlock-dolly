# Local model checker

The checker is vendored unchanged from
[paralin/tla-skill](https://github.com/paralin/tla-skill) commit
`3dee8fbafa1e6ae4b6383dc38d3b502adda01747`, under its retained MIT license.
The lockfile pins [tla-ts](https://github.com/aidenybai/tla-ts) 0.0.8 and its
dependencies. Upstream workflow instructions are not required to run the check.

From the repository root, install Node 20 or newer and run:

```powershell
npm ci --prefix tools/tla --ignore-scripts
python tools/check_tla.py
```

The Python entry point also rejects missing required model configurations;
upstream's directory runner permits an empty directory to pass. Add a component's
configuration to REQUIRED when extending mandatory coverage. `--json` retains
upstream results and counterexample traces. No dependency is added to Dolly's
packaged runtime. See [SPECS.md](../../SPECS.md) for coverage and limitations.
