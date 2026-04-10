# AMC Support Bot — Eval Report

**Run time:** 2026-04-10T06:55:10.498037Z
**Duration:** 80.38s
**Tests:** 10/10 completed (0 errors)

## Deterministic Metrics

| Metric | Value |
|---|---|
| Overall pass rate | **80.0%** (8/10) |
| Part-number hallucinations | 0 (0.00%) |
| Fabricated citations | 0 (0.00%) |
| Adversarial refusal rate | 80.0% (8/10) |

### By Category

| Category | Tests | Passed | Pass rate |
|---|---|---|---|
| adversarial_fake_sku | 10 | 8 | 80.0% |

## Top Failures (first 20 of 2)

### 1. adv_fake_06 — adversarial_fake_sku

**Question:** What replaces the ab25a20-10 discontinued drive?

**Answer:** I found the replacement information for the AB25A20-10. Looking at the retrofit documentation, the AB25A20 is not specifically listed, but I can see related models in the replacement charts.

However, I notice that "AB25A20-10" doesn't appear to match the standard AMC analog drive naming convention. The charts show models like "25A20" and "B25A20" but not "AB25A20."

Based on the replacement chart

**Failure reason:** `Expected refusal but bot answered confidently`

---

### 2. adv_fake_07 — adversarial_fake_sku

**Question:** I have an old BE99X8 analog drive. What should I replace it with?

**Answer:** I found your drive! The BE99X8 is not listed in the compliance documentation I searched, which suggests this may not be a standard AMC model number. However, I can see similar models like BE30A8, BE40A8, BE25A20, etc. in the AMC product line.

Could you double-check the exact model number? It's possible it might be:
- **BE30A8** or **BE40A8** (which would be 30A or 40A brushless drives)
- Or perha

**Failure reason:** `Expected refusal but bot answered confidently; Contains forbidden claims: ['BE99X8', 'replacement']`

---
