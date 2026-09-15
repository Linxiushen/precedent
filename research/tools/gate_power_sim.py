#!/usr/bin/env python3
"""Monte-Carlo: statistical power of a PACE-style anytime-valid PAIRED gate on binary task outcomes.
Setup: N paired tasks (same task run with incumbent vs candidate). Per task, outcome pair (y_inc, y_cand) in {0,1}^2.
We only learn from DISCORDANT pairs (one passes, other fails). Under H0 (no lift) discordant pairs are 50/50.
Test: a betting/test-martingale on discordant pairs with a mixture over p in (0.5,1) -> anytime-valid (Ville's inequality);
commit when wealth >= 1/alpha. Also report a per-task 'never-regress floor' false-alarm rate at 1 vs 2 repeats.
"""
import random, math, statistics, sys
random.seed(7)
def run(N, p_inc, lift, alpha=0.05, reps=4000, k_repeats=1):
    p_cand=min(1.0,p_inc+lift)
    commits=0; stop_at=[]
    for _ in range(reps):
        wealth=1.0; committed=False
        # mixture bet: grid over q in {0.6,0.7,0.8,0.9}, equal weights -> wealth = mean of individual martingales
        grid=[0.6,0.7,0.8,0.9]; w=[1.0]*len(grid)
        for i in range(N):
            yi=1 if random.random()<p_inc else 0
            yc=1 if random.random()<p_cand else 0
            if yi==yc: continue
            for gi,q in enumerate(grid):
                w[gi]*= (2*q) if yc>yi else (2*(1-q))
            if sum(w)/len(w) >= 1/alpha:
                committed=True; stop_at.append(i+1); break
        commits+=committed
    return commits/reps, (statistics.median(stop_at) if stop_at else None)

print("PAIRED ANYTIME-VALID GATE (alpha=0.05). p_inc = incumbent pass rate. Rows: true lift. Cells: commit rate (median #tasks to commit)")
for p_inc in (0.5,0.7):
    print(f"\n-- incumbent pass rate {p_inc} --")
    print(f"{'lift':>6} | " + " | ".join(f"N={N:>3}" for N in (20,40,80,160)))
    for lift in (0.0,0.05,0.10,0.15,0.20,0.30):
        row=[]
        for N in (20,40,80,160):
            r,med=run(N,p_inc,lift)
            row.append(f"{r:5.2f} ({med if med else '-':>3})")
        print(f"{lift:6.2f} | " + " | ".join(row))

print("\nGREEDY 'keep if mean went up' on N tasks, zero true lift -> false-commit rate:")
for N in (20,40,80):
    fc=0; reps=4000
    for _ in range(reps):
        a=sum(random.random()<0.6 for _ in range(N)); b=sum(random.random()<0.6 for _ in range(N))
        fc+= b>a
    print(f"  N={N}: {fc/reps:.2f}")

print("\nPER-TASK NEVER-REGRESS FLOOR, zero true lift, task pass prob 0.6: P(at least one task flips pass->fail) i.e. false block rate")
for N in (10,20,40):
    for k in (1,2,3):
        blocks=0; reps=3000
        for _ in range(reps):
            blocked=False
            for _t in range(N):
                inc_pass = all(random.random()<0.6 for _ in range(k))   # protected corpus = tasks incumbent passed k/k
                if not inc_pass: continue
                cand_fail_all = all(random.random()>=0.6 for _ in range(k))  # candidate fails k/k -> flagged
                if cand_fail_all: blocked=True; break
            blocks+=blocked
        print(f"  N={N:3} tasks, k={k} repeats each: false-block {blocks/reps:.2f}")
