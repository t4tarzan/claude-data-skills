# Selection & DAG Resolver (P0.3)

"Pick any subset in any order" only works if something guarantees each chosen stage can
actually get the inputs it needs. The **resolver** is that something. It runs once, before
any stage executes, over the user's selection.

## Input

- **`selection`**: the stages the user asked for, e.g. `--stages analyst` or
  `--stages architect,engineer,designer,analyst` (order as given is a *preference*, not a
  constraint — the resolver topologically sorts).
- **`supplied`**: artifacts the user already has and points at, e.g.
  `--gold ./my_warehouse/` or `--model ./model.pkl`. These satisfy a `consumes` key
  without running the producing stage.

## The dependency graph

From the contract ([P0.2](01-artifact-contract.md)), each stage's `consumes` keys have a
canonical **producer**:

```
sources → (user)          bronze → architect     silver,gold → engineer
semantic,conformance → designer                  reports,visuals → analyst
model,eval → scientist    service → ml           dashboard → bi     deployment → sre
```

## Algorithm

For each stage in `selection`, resolve every required `consumes` key:

1. **Supplied?** If the key is in `supplied`, mark satisfied (record provenance
   `supplied` in the manifest). Done.
2. **Produced by another selected stage?** Keep it; add the dependency edge.
3. **Neither** → the key is *missing upstream*. Apply the missing-input policy (below).

Then **topologically sort** the resulting graph (spine order breaks ties) and check for
cycles (there are none in a valid selection — the contract graph is a DAG). Execute in
that order.

### Missing-input policy (default → configurable)

When a required key is neither supplied nor produced by a selected stage:

- **`synthesize` (default):** auto-insert the minimal producing stage(s), transitively,
  and tell the user (`log`: "analyst needs gold → auto-adding engineer, architect"). This
  is the friendliest behavior and matches how you'd expect `--stages analyst` on a folder
  of CSVs to "just work."
- **`ask`:** stop and ask the user (AskUserQuestion) whether to auto-add the upstream
  stages or supply the artifact.
- **`strict`:** refuse with a precise error — "stage `analyst` needs `gold`; supply
  `--gold <path>` or add `engineer` to `--stages`." No implicit work.

Optional consumes (`semantic?`, `reports?`) never trigger the policy — the stage just runs
degraded and notes it in the manifest (`status: partial`).

## Worked examples

| Command | Resolves to |
|---|---|
| `--stages analyst` (folder of CSVs, default policy) | `architect → engineer → analyst` (designer skipped: `semantic` is optional for analyst) |
| `--stages analyst --gold ./wh/` | `analyst` alone, consuming supplied gold |
| `--stages designer,analyst --gold ./wh/` | `designer → analyst`, both on supplied gold |
| `--stages ml` (strict) | error: "`ml` needs `model`; supply `--model` or add `scientist`" |
| `--stages sre --service ./svc/` | `sre` alone, wrapping the supplied deployable |
| `--stages architect,engineer,designer,analyst` | the full spine, in order |

## Guarantees

- Every executed stage has all required inputs present on disk before it starts.
- The run is a **DAG** — deterministic order, no partial-input surprises.
- What the resolver decided (added stages, supplied artifacts, chosen policy) is written
  to `run/manifest.json → plan` so the run is self-describing and reproducible.

The resolver is pure planning — it never transforms data. It emits the ordered execution
plan the runner (P5.1) then walks.
