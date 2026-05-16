## Summary

-

## Type

- [ ] Bug fix
- [ ] Feature
- [ ] Docs
- [ ] Tests or tooling
- [ ] Refactor

## Verification

Paste the commands you ran and the result.

```bash
pytest -q
```

For workflow/service/board changes:

```bash
symphony doctor ./WORKFLOW.md
```

## Risk Notes

-

## Checklist

- [ ] This PR targets `dev`.
- [ ] I added or updated tests for behavior changes.
- [ ] I updated docs/examples for user-facing changes.
- [ ] I did not commit secrets, logs, local run state, virtualenvs, or generated artifacts.
- [ ] CI is green or I explained any expected failure above.
