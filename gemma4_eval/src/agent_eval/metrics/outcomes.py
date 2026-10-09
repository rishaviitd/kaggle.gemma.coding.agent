"""Known-outcome denominators and Wilson binomial intervals."""
import math

def outcome_summary(runs):
    known = runs.resolved_nullable.dropna()
    n=len(known); solved=int(known.eq(True).sum()); total=len(runs)
    if n:
        z=1.959963984540054;p=solved/n;den=1+z*z/n
        center=(p+z*z/(2*n))/den
        half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
        interval=[max(0,center-half), min(1,center+half)]
    else:
        interval=[None,None]
    return dict(total_runs=total,known_outcomes=n,resolved=solved,unresolved=n-solved,unknown_outcomes=total-n,resolved_rate=solved/n if n else None,outcome_coverage=n/total if total else None,wilson_95=interval)
