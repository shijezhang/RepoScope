# RepoScope analysis

Run: `example-click-01` · Revision: 1 · partial
Base: `934813e4d421071a1b3db3973c02fe2721359a6e`
Head: `0ccf529f8dfa7b4c938a6f858840960661ee830d`

## Potential impact

- [base] `src/click/types.py:449` `_NumberRangeBase.convert` · distance 0 · evidence `adde7c63b3203ae4af87fc28d615776698875976193f7be253aa9de0b92e2f02`
- [head] `src/click/types.py:449` `_NumberRangeBase.convert` · distance 0 · evidence `84a4595ef0893b6801819ee03af1cb5bb530d625431b28799287c84fe9577ca0`

## Test plan

Status: collection_required
- Coverage unavailable: collect and run the full registered test pool

## Execution

```json
[]
```

## Limitations

- old: 175 unresolved/candidate call sites in affected files
- new: 175 unresolved/candidate call sites in affected files
- Tests have not been collected or executed for this plan
- No valid version-bound coverage imported; test selection must fall back conservatively