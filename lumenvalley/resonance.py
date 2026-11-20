"""Prism Resonance: the Phase 7 dash-through traversal mechanic.

A prism node is a non-solid ring placed in the level.  Dashing through an armed
ring refuels the air dash, extends the current dash and adds one link to the
airborne PRISM chain.  Landing (or respawning) re-arms every ring and clears the
chain, so a single airtime can never hover forever.

This module is deliberately pure: it knows node positions and a player-shaped
rectangle, not a renderer, audio player or the rest of the simulation.  That is
what makes the mechanic unit-testable and keeps it from quietly changing the
Phase 2 physics trace.
"""

#: How close a node centre must be to the player rectangle to count as a hit.
#: The rings are drawn at ~0.8 cells, so this is forgiving without letting a
#: dash collect a node it visibly missed.
NODE_RADIUS = 0.85

#: Score for the first ring, multiplied by the current chain link.  The second
#: airborne ring is worth 200, the third 300, and so on.
CHAIN_SCORE = 100


def node_center(node):
    """Centre of a one-cell level glyph, in grid cells."""
    return (node[0] + 0.5, node[1] + 0.5)


def _point_rect_distance_sq(cx, cy, rect):
    x, y, w, h = rect
    nx = min(max(cx, x), x + w)
    ny = min(max(cy, y), y + h)
    dx = cx - nx
    dy = cy - ny
    return dx * dx + dy * dy


def overlaps(node, rect, radius=NODE_RADIUS):
    """True when *node* is within *radius* cells of the player rectangle."""
    cx, cy = node_center(node)
    return _point_rect_distance_sq(cx, cy, rect) <= radius * radius


def chain_points(chain):
    """Points for the n-th ring of an airborne chain (first = 100)."""
    return CHAIN_SCORE * max(0, int(chain))


class ResonanceField:
    """Per-airtime node state and scoring for one level."""

    def __init__(self, nodes=()):
        self.nodes = tuple(sorted({(int(x), int(y)) for x, y in nodes}))
        self.spent = set()
        self.chain = 0
        self.best_chain = 0
        self.hits = 0
        self.last_hit = None        # (node, game-time seconds)

    @property
    def total(self):
        return len(self.nodes)

    def armed(self, node):
        return node in self.nodes and node not in self.spent

    def rearm(self):
        """Landing/respawn: every ring is ready again and the chain resets."""
        self.spent.clear()
        self.chain = 0
        self.last_hit = None

    def scan(self, rect, dashing, t=0.0):
        """Spend and return the nearest armed node hit by a dash, else None."""
        if not dashing or not self.nodes:
            return None
        best = None
        best_key = None
        for node in self.nodes:
            if node in self.spent:
                continue
            cx, cy = node_center(node)
            dist2 = _point_rect_distance_sq(cx, cy, rect)
            if dist2 > NODE_RADIUS * NODE_RADIUS:
                continue
            key = (dist2, node)
            if best_key is None or key < best_key:
                best, best_key = node, key
        if best is None:
            return None
        self.spent.add(best)
        self.chain += 1
        self.best_chain = max(self.best_chain, self.chain)
        self.hits += 1
        self.last_hit = (best, float(t))
        return best

    def flash_amount(self, t, duration=0.28):
        """1.0 right after a hit, fading to 0.0; renderer-only conveniences."""
        if self.last_hit is None:
            return 0.0
        age = float(t) - self.last_hit[1]
        if age < 0 or age >= duration:
            return 0.0
        return 1.0 - age / duration
