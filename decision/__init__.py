"""Decision models: small local models that pick one answer from a fixed list.

    from decision import IntentModel, Decision, Gate

`IntentModel` reads one message and picks an action from `core.interface.ACTIONS` with a confidence, in pure
Python and in well under a millisecond. `Gate` puts it before the generative model (direct answers for
requests with nothing to fill in), after it (a cross-check that can only turn a payment reading into a
question) and in its place when no generative model answered. See decision/README.md.

Nothing here imports scikit-learn or numpy; only decision/train.py does.
"""

from decision.gate import Gate
from decision.intent import Decision, IntentModel

__all__ = ["Decision", "Gate", "IntentModel"]
