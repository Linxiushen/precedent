<!-- What this changes, and the claim it makes. -->

## What breaks if this is wrong

<!-- Every gate change can fail in two directions. Say which one this risks:
     letting something through that should have been stopped, or stopping
     something that should have gone through. -->

## Evidence

- [ ] `./scripts/dev.sh --tests` passes (receipts, acceptor, precedent)
- [ ] `cd plugins/openclaw && npm test` passes, if the plugin changed
- [ ] A test that **fails without this change** — named here:
- [ ] Numbers in prose are reproduced by a command in the repo, and that command is written next to them

## If this touches the acceptance path

- [ ] No fix makes a null easier to pass — a gate that accepts more is not a fixed gate
- [ ] `python -m acceptor.bench` still shows greedy at 40–50 % false commits and the gate at or under alpha
