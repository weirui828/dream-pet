"""An alternative drive model, registered via entry points to show the plug-in seam.

CountBasedBoredom ignores learnability and habituation: satisfaction is novelty alone
(count/density novelty, Bellemare et al. 2016). Useful as a baseline in sim comparisons.
"""

from __future__ import annotations

from dreampet.drives.base import Event
from dreampet.drives.hybrid import HybridLPBoredom


class CountBasedBoredom(HybridLPBoredom):
    name = "count_based"

    def satisfaction(self, e: Event) -> float:
        p = e.payload
        if "novelty" not in p:
            return 0.0
        return self.kind_weight(e.type) * float(p["novelty"]) * 0.5
