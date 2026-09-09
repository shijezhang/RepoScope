# RepoScope analysis

Run: `example-fixture-04` · Revision: 1 · partial
Base: `f68014c0a2919d82333ab6b2e5c9027af8773ea1`
Head: `aebd5c279a69fa36b4c7932b3949d84e31bca36d`

## Potential impact

- [base] `api.py:12` `plugin` · distance 0 · evidence `612d11ba9114fd7de921abb4acc96ead8ea399eb93dd7c08020a13ee3f2d46bb`
- [head] `api.py:12` `plugin` · distance 0 · evidence `3bcba6e252bee422de8385a41db77c736ab2e7dd05d16680f32bf0ddac5fc4c5`
- [base] `test_calc.py:1` `<module>` · distance 1 · evidence `7332c0027e17493765cf2fa0542be979054e59bed01d664110dd10f348284889`
- [head] `test_calc.py:1` `<module>` · distance 1 · evidence `7c1d718a7c9121804ebfd2b41065ce67c3911a00023ed0080744a1f37e461cb0`
- [base] `test_calc.py:16` `test_plugin` · distance 1 · evidence `8c9aa25bda4be9f9d6c492c9afa7549b6f552e54f58cc7362a06af26e07d6a73`
- [head] `test_calc.py:16` `test_plugin` · distance 1 · evidence `e63eb6c3efa8f1c512f33962f307d28e51073e532fb8110d1c04f00aa9529f7d`
- [base] `test_calc.py:4` `test_positive` · distance 2 · evidence `f9fb6083aaef0777d8339683e3f6c7e606c13c264c84c10a5917e58e37055cb0`
- [head] `test_calc.py:4` `test_positive` · distance 2 · evidence `d118abe1c25372c22773135999580698ccbcbbcbe149a1d91d659102c6558d78`
- [base] `test_calc.py:8` `test_negative` · distance 2 · evidence `e756705d03cac2aae3d6784fe296c6486d3e11d956cf0a459006f4345cc6ff93`
- [head] `test_calc.py:8` `test_negative` · distance 2 · evidence `2c4eedb7b29bdd06ed37f2556d3eef2db035bb9bf2b012ebcb6c5b68936d3af8`
- [base] `test_calc.py:12` `test_legacy` · distance 2 · evidence `9ec3b5f704d9296d6b93598774be5f376c4c74981cc8ea958ea6d23502a78c61`
- [head] `test_calc.py:12` `test_legacy` · distance 2 · evidence `48d50f17a69b21609b87eff90ceeab637d8945f841826ea64cfbea52902e6bdd`

## Test plan

Status: collection_required
- Coverage unavailable: collect and run the full registered test pool

## Execution

```json
[]
```

## Limitations

- old: 4 unresolved/candidate call sites in affected files
- new: 4 unresolved/candidate call sites in affected files
- Tests have not been collected or executed for this plan
- No valid version-bound coverage imported; test selection must fall back conservatively